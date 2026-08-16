#!/usr/bin/env python3
"""
/car -- "continue after reset".  A Claude Code UserPromptSubmit hook.

Claude Code makes no API request until every UserPromptSubmit hook has returned, so all
the waiting here happens client-side and invoking /car costs zero tokens -- which is the
point: it is meant to be usable when the usage limit is already spent.

Everything turns on whether the account is blocked *right now* -- meaning either the last
assistant message is a usage-limit error, or a persisted window is at >=99% utilisation.

                   | already limited          | not limited
  -----------------+--------------------------+---------------------------------------
  /car             | wait, then "Continue."   | send NOTHING (exit 2)
  /car <prompt>    | wait, then <prompt>      | run <prompt> immediately (exit 0)
  /car --wait      | wait, then "Continue."   | wait anyway (detection override)

TO SURVIVE A LIMIT DURING LONG WORK, SEND TWO PROMPTS:
  1. your prompt, as normal
  2. a bare /car right after it -- typed while the first is still running, so it queues
If the limit kills the turn, the queued /car waits for the reset and continues the work.
If the work finishes cleanly, the queued /car sends nothing.  Nothing is wasted either way.
The "send nothing" cell above is exactly what makes step 2 free.

It has to be two prompts.  A one-shot "/car <prompt>" cannot do it *in this session*:
this hook returns before your prompt starts running, and Claude Code fires no hook when a
turn later dies on the limit (Stop fires only on success).  Nor can anything type into a
live session.  A queued prompt, though, does survive that dead turn, which is the
mechanism the two-prompt recipe relies on.

"/car <prompt>" therefore differs from a plain "<prompt>" only when you are already
limited: it waits for the reset instead of failing.  Otherwise it passes straight through.

--wait forces the wait when detection says otherwise.  Mainly for: you hit the limit,
restarted Claude Code, so this transcript has no limit error and utilisation reads 97%
-- bare /car would wrongly refuse.  Also useful to deliberately pause until the next
window when you have budget left.

Reset times come from <config>/rate-limit-state.json, written on every render by the
status-line command (car_usage_recorder.py) from the rate_limits block Claude Code passes
it, and as a fallback from the "resets 3:40am" text in the limit error itself.  Nothing
here talks to the network.

Environment overrides:
  CAR_STATE      path to rate-limit-state.json
  CAR_GRACE      seconds to wake *after* the reset          (default 60)
  CAR_MAX_WAIT   refuse waits longer than this, in seconds  (default 28800 = 8h)
  CAR_DRY_RUN    set to anything: report the verdict and exit without sleeping
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

GRACE = int(os.environ.get("CAR_GRACE", 60))
MAX_WAIT = int(os.environ.get("CAR_MAX_WAIT", 8 * 3600))


def config_dir():
    """Claude Code's config directory: $CLAUDE_CONFIG_DIR, else ~/.claude."""
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )


def to_epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)                                    # epoch seconds
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()  # ISO-8601
    except ValueError:
        return None


def find_limit_error(transcript, now_ts):
    """Did the last turn die on the usage limit?

    Claude Code records that as an assistant message with isApiErrorMessage set and text
    like "You've hit your session limit - resets 3:40am (Europe/London)".
    """
    if not transcript or not os.path.exists(transcript):
        return None, None
    try:
        with open(transcript, "rb") as fh:                 # tail, transcripts get large
            fh.seek(0, os.SEEK_END)
            back = min(fh.tell(), 400_000)
            fh.seek(-back, os.SEEK_END)
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None, None

    for line in reversed(lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        content = d.get("message", {}).get("content")
        text = content if isinstance(content, str) else " ".join(
            b.get("text", "") for b in content or [] if isinstance(b, dict))
        if d.get("isApiErrorMessage") and re.search(r"hit your .*limit", text, re.I):
            ts = to_epoch(d.get("timestamp"))
            # Only counts if it is from the turn that just ended, not ancient history.
            if ts is None or now_ts - ts < 6 * 3600:
                return text, ts
        break                       # only the *last* assistant message counts
    # A queued /car lands right after the failed turn, so the error is last.  If the user
    # typed something since, the newest user message would follow it and we would not see
    # it here -- which is the correct outcome: that work is no longer resumable.
    return None, None


def windows(rl):
    for name, w in (rl or {}).items():
        if not isinstance(w, dict):
            continue
        epoch = to_epoch(w.get("resets_at", w.get("resetsAt")))
        if epoch is None:
            continue
        used = w.get("used_percentage", w.get("utilization"))
        try:
            used = float(used)
        except (TypeError, ValueError):
            used = 0.0
        yield name, used, epoch


def load_cached(now_ts):
    """~/.claude.json's cached utilisation.  Only refreshed occasionally (e.g. by /usage),
    so it is often days old -- a stale reset time here would read as 'already reset' and
    pre-empt better sources.  Use it only while it is fresh."""
    for path in (os.path.join(os.path.expanduser("~"), ".claude.json"),
                 os.path.join(config_dir(), ".claude.json")):
        try:
            with open(path) as fh:
                cached = json.load(fh)["cachedUsageUtilization"]
            if now_ts - float(cached.get("fetchedAtMs", 0)) / 1000 > 6 * 3600:
                continue
            return cached["utilization"]
        except Exception:
            continue
    return None


def verdict(state_path, transcript):
    now_ts = datetime.now().astimezone().timestamp()

    limit_text, limit_ts = find_limit_error(transcript, now_ts)

    # --- Utilisation from the persisted rate_limits block ---------------------------
    rl = None
    try:
        with open(state_path) as fh:
            rl = json.load(fh)
    except Exception:
        rl = None
    if rl is None:
        rl = load_cached(now_ts)

    ws = list(windows(rl))
    util_blocked = any(used >= 99 for _, used, _ in ws)

    # --- When does the blocking window reset? ---------------------------------------
    target = None
    if ws:
        maxed = [e for _, used, e in ws if used >= 95]
        if maxed:
            target = max(maxed)     # several maxed -> the last one to reset unblocks us
        else:
            five = [e for n, _, e in ws if "five" in n or "hour" in n or n == "session"]
            target = max(five) if five else min(e for _, _, e in ws)

    # Only if there is no recorded reset at all, recover the clock time from the error
    # text ("... resets 3:40am").  Anchor it to when the error happened, not to now: a
    # reset is at most one window after the error, so the next HH:MM at or after the
    # error is the one.  Anchoring to now would turn an already-past reset into "same
    # time tomorrow".
    if target is None and limit_text and limit_ts:
        m = re.search(r"resets\s+(\d{1,2}):(\d{2})\s*([ap]m)", limit_text, re.I)
        if m:
            hour, minute, ampm = int(m.group(1)) % 12, int(m.group(2)), m.group(3).lower()
            if ampm == "pm":
                hour += 12
            base = datetime.fromtimestamp(limit_ts).astimezone()
            cand = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if cand < base:
                cand += timedelta(days=1)
            target = cand.timestamp()

    return {
        "blocked": bool(limit_text) or util_blocked,
        "reason": "limit error in transcript" if limit_text else (
            "window at >=99%" if util_blocked else "not limited"),
        "target": int(target) if target else None,
    }


def notify(message):
    """Best-effort desktop notification; the terminal bell is the universal fallback."""
    if os.environ.get("CAR_NO_NOTIFY"):
        return
    sys.stderr.write("\a")
    sys.stderr.flush()
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["osascript", "-e",
                 'display notification "%s" with title "Claude Code /car"' % message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["notify-send", "Claude Code /car", message],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command",
                 "[console]::beep(880,400)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def hm(epoch, fmt="%H:%M"):
    try:
        return datetime.fromtimestamp(epoch).strftime(fmt)
    except Exception:
        return "?"


def main():
    raw = sys.stdin.read()

    # This hook runs on every prompt, so reject non-/car prompts before doing any work.
    if "/car" not in raw:
        return 0
    try:
        data = json.loads(raw)
    except ValueError:
        return 0

    prompt = (data.get("prompt") or "").strip()
    if not (prompt == "/car" or prompt.startswith("/car ")):
        return 0

    transcript = data.get("transcript_path") or ""
    state = os.environ.get("CAR_STATE") or os.path.join(
        config_dir(), "rate-limit-state.json")
    log = os.path.join(config_dir(), "car.log")

    # Bare /car (no arguments) means "resume the interrupted work"; with arguments it is
    # "run this, after the reset if necessary".
    # "/car --wait" forces the wait even when we cannot prove the account is blocked --
    # the escape hatch for when limit detection is wrong (see the not-limited branch).
    args = prompt[len("/car"):].strip()
    force = args in ("--wait", "-w")
    if force:
        args = ""
    has_args = bool(args)

    v = verdict(state, transcript)
    blocked, target, reason = v["blocked"], v["target"], v["reason"]

    # --- Not limited ----------------------------------------------------------------
    if not blocked and not force:
        if has_args:
            return 0            # /car <prompt> with a healthy account: just run it now
        sys.stderr.write(
            "/car: the limit was never hit - nothing to continue, so nothing was sent.\n"
            "  (no tokens used; %s)\n"
            "  If that is wrong, '/car --wait' waits for the next reset regardless.\n"
            % reason)
        return 2

    if not target:
        sys.stderr.write(
            "/car: you appear to be rate-limited but no reset time is on record.\n"
            "  Expected: %s\n"
            "  It is written by the status-line command (car_usage_recorder.py) once\n"
            "  Claude Code reports usage. Send one normal prompt, then retry /car.\n"
            % state)
        return 2

    now = int(time.time())
    wake = target + GRACE
    reset_h = hm(target)

    if wake <= now:
        print("[car] Usage limit already reset (at %s). Resuming now." % reset_h)
        return 0

    left = wake - now
    if left > MAX_WAIT:
        sys.stderr.write(
            "/car: the blocking window does not reset until %s - %dh%02dm away.\n"
            "That is past the %dh cap (CAR_MAX_WAIT), so /car is not waiting.\n"
            % (hm(wake, "%a %H:%M"), left // 3600, left % 3600 // 60, MAX_WAIT // 3600))
        return 2

    if os.environ.get("CAR_DRY_RUN"):
        sys.stderr.write(
            "blocked (%s); would wait %dh%02dm, until %s (reset %s + %ds)\n"
            % (reason, left // 3600, left % 3600 // 60,
               hm(wake, "%Y-%m-%d %H:%M:%S"), reset_h, GRACE))
        return 2

    line = ("%s  /car waiting %dh%02dm, until %s (reset %s + %ds; %s)\n"
            % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               left // 3600, left % 3600 // 60,
               hm(wake, "%Y-%m-%d %H:%M:%S"), reset_h, GRACE, reason))
    try:
        with open(log, "a") as fh:
            fh.write(line)
    except OSError:
        pass

    # Sleep in short slices so a suspended/resumed laptop cannot overshoot silently.
    while True:
        now = int(time.time())
        if now >= wake:
            break
        time.sleep(min(30, wake - now))

    try:
        with open(log, "a") as fh:
            fh.write("%s  /car resuming\n"
                     % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except OSError:
        pass

    notify("Usage limit reset - resuming.")
    print("[car] Waited out the usage limit; the window reset at %s. "
          "Pick up where you left off." % reset_h)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\n/car: wait cancelled; nothing was sent.\n")
        sys.exit(2)
