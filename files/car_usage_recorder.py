#!/usr/bin/env python3
"""
Claude Code status-line command that records rate-limit state for /car.

WHY THIS EXISTS
  /car has to know when the usage window resets *while the limit is spent*, so it cannot
  ask the API.  The status line is the only place Claude Code hands out the `rate_limits`
  block, so this script persists it to <config>/rate-limit-state.json on every render.
  Without it, /car can only fall back to parsing "resets 3:40am" out of the limit error.

IT DOES NOT REPLACE YOUR STATUS LINE
  If <config>/car-inner-statusline exists, the command inside it is run with the same
  stdin and its output becomes the status line -- so this is a transparent shim in front
  of whatever you already had.  The installer writes that file for you.
  If it does not exist, a compact default line is printed instead.
"""

import json
import os
import subprocess
import sys
from datetime import datetime


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )


def record(data, cfg):
    """Persist the whole rate_limits block, atomically."""
    rl = data.get("rate_limits")
    if not rl:
        return
    dest = os.path.join(cfg, "rate-limit-state.json")
    tmp = "%s.%d" % (dest, os.getpid())
    try:
        with open(tmp, "w") as fh:
            json.dump(rl, fh)
        os.replace(tmp, dest)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def delegate(raw, cfg):
    """Run the user's original status-line command, if one was saved.  True if handled."""
    path = os.path.join(cfg, "car-inner-statusline")
    try:
        with open(path) as fh:
            cmd = next((ln.strip() for ln in fh
                        if ln.strip() and not ln.lstrip().startswith("#")), "")
    except OSError:
        return False
    if not cmd:
        return False
    try:
        p = subprocess.run(cmd, shell=True, input=raw.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    if p.returncode != 0 and not p.stdout.strip():
        return False            # inner command is broken -- show something, not a blank
    sys.stdout.write(p.stdout.decode("utf-8", "replace"))
    return True


DIM = "\033[2;%dm"
RESET = "\033[0m"


def default_line(data):
    seg = []

    model = (data.get("model") or {}).get("display_name")
    if model:
        seg.append((36, model))

    remaining = (data.get("context_window") or {}).get("remaining_percentage")
    if remaining is not None:
        try:
            seg.append((35, "ctx %.0f%%" % (100 - float(remaining))))
        except (TypeError, ValueError):
            pass

    rl = data.get("rate_limits") or {}
    for key, label in (("seven_day", "7d"), ("five_hour", "5h")):
        used = (rl.get(key) or {}).get("used_percentage")
        if used is not None:
            try:
                seg.append((37 if key == "seven_day" else 31,
                            "%s %.0f%%" % (label, float(used))))
            except (TypeError, ValueError):
                pass

    resets = (rl.get("five_hour") or {}).get("resets_at")
    if resets:
        try:
            t = datetime.fromtimestamp(float(resets)).strftime("%I:%M%p")
            seg.append((31, "↺ " + t.lstrip("0").lower()))
        except (TypeError, ValueError, OSError):
            pass

    ws = data.get("workspace") or {}
    cwd = ws.get("current_dir") or data.get("cwd")
    if cwd:
        seg.append((33, cwd))

    return " | ".join((DIM % c) + t + RESET for c, t in seg)


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except ValueError:
        return 0

    cfg = config_dir()
    record(data, cfg)

    if not delegate(raw, cfg):
        sys.stdout.write(default_line(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
