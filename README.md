# `/car` — continue after a usage limit

A custom Claude Code slash command that survives hitting the usage limit. You queue it
behind long-running work; if the limit kills the turn, `/car` sleeps until the window
resets and then tells Claude to carry on. If the work finishes fine, `/car` sends nothing.

It costs **zero tokens** to invoke. Claude Code makes no API request until every
`UserPromptSubmit` hook has returned, so all the waiting happens on your machine — which
is the whole point: it has to work when your limit is already spent.

---

## The recipe (this is the part that matters)

**Send two prompts:**

1. Your real prompt, as normal — *"refactor the parser and update all the tests"*
2. A bare `/car`, typed **while the first one is still running**, so it queues behind it

Then walk away.

- Limit kills the turn → the queued `/car` wakes up, waits out the reset, and sends
  "Continue." so Claude picks the work back up.
- Work finishes cleanly → the queued `/car` sends nothing at all. Nothing wasted.

That second behaviour is what makes step 2 free, so there is never a reason not to do it.

### Full behaviour

|                | already limited          | not limited                     |
| -------------- | ------------------------ | ------------------------------- |
| `/car`         | wait, then "Continue."   | send **nothing** (exit 2)       |
| `/car <prompt>`| wait, then `<prompt>`    | run `<prompt>` immediately      |
| `/car --wait`  | wait, then "Continue."   | wait anyway (detection override)|

`/car --wait` is the escape hatch for when detection is wrong — e.g. you hit the limit,
restarted Claude Code, so this transcript has no limit error and usage reads 97%. It is
also handy to deliberately pause until the next window while you still have budget.

---

## Install

Requires **Python 3.7+** and Claude Code 2.x. Nothing else — no `jq`, no Homebrew.

```bash
cd car-command
python3 install.py
```

Windows (PowerShell or CMD):

```powershell
cd car-command
py install.py
```

Then **restart Claude Code**.

Useful flags:

| Flag | What it does |
| --- | --- |
| `--dry-run` | Print every change, write nothing |
| `--uninstall` | Remove it and restore your previous status line |
| `--python /path/to/python3` | Pin the interpreter the hook runs under |

`settings.json` is backed up to `settings.json.bak-<timestamp>` before it is edited, and
the installer merges — your existing hooks, model, theme and permissions are left alone.
Running it twice is safe.

### Verify it worked

1. `/hooks` → `car_wait.py` should be listed under **UserPromptSubmit**.
2. Type `/car` on a healthy account. You should get:

   ```
   /car: the limit was never hit - nothing to continue, so nothing was sent.
   ```

   **That message is a pass.** It means the hook ran, decided you are fine, and blocked
   the prompt before it cost anything.

3. Optional dry run of the waiting logic, without waiting:

   ```bash
   echo '{"prompt":"/car --wait","transcript_path":""}' | CAR_DRY_RUN=1 python3 ~/.claude/car_wait.py
   ```

---

## What gets installed

| Path | Purpose |
| --- | --- |
| `~/.claude/commands/car.md` | The `/car` slash command itself — just sends "Continue." |
| `~/.claude/car_wait.py` | The `UserPromptSubmit` hook. All the logic lives here. |
| `~/.claude/car_usage_recorder.py` | Status-line command; records when your limit resets |
| `~/.claude/car-inner-statusline` | Your *previous* status-line command, so it still runs |
| `~/.claude/car.log` | One line per wait and per resume (created on first use) |
| `~/.claude/rate-limit-state.json` | The persisted `rate_limits` block (created on first render) |

Plus two entries in `~/.claude/settings.json`: the hook registration and the status line.

If `CLAUDE_CONFIG_DIR` is set, everything goes there instead of `~/.claude`.

### Why it touches the status line

`/car` has to know **when the window resets while the limit is spent**, so it cannot ask
the API. The status line is the only place Claude Code hands out the `rate_limits` block,
so `car_usage_recorder.py` persists it on every render.

It does **not** replace an existing status line. If you already had one, the installer
moves it into `~/.claude/car-inner-statusline` and the recorder runs it and prints its
output verbatim — a transparent shim. If you had none, you get a compact default line
(`model | ctx % | 7d % | 5h % | ↺ reset | cwd`). Delete `car-inner-statusline` to switch
to that default; edit it to change what runs.

Without the recorder `/car` still works, but only by scraping "resets 3:40am" out of the
limit error text, which is less reliable.

---

## Options

Environment variables, all optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CAR_GRACE` | `60` | Seconds to wake *after* the reset time |
| `CAR_MAX_WAIT` | `28800` (8h) | Refuse waits longer than this — stops a weekly-limit reset parking you for two days |
| `CAR_DRY_RUN` | unset | Report the verdict and exit instead of sleeping |
| `CAR_NO_NOTIFY` | unset | Suppress the desktop notification on resume |
| `CAR_STATE` | — | Override the path to `rate-limit-state.json` |

The hook's `timeout` in `settings.json` is **30000 seconds** (~8.3h), deliberately just
above `CAR_MAX_WAIT`. If you raise `CAR_MAX_WAIT`, raise that too or Claude Code will kill
the hook mid-wait.

---

## OS notes

Everything is plain Python, so it runs the same on macOS, Linux and Windows.

- **macOS** — desktop notification on resume via `osascript`.
- **Linux** — notification via `notify-send` if installed; terminal bell otherwise.
- **Windows** — a beep; no toast. Works in PowerShell, CMD, WSL and Git Bash.
- **WSL** — install inside WSL, where Claude Code actually runs.

Rate-limit percentages only appear for Claude subscription accounts (Pro/Max). On an API
key you get no `rate_limits` block, so `/car` falls back to reading the reset time out of
the limit error message.

---

## Troubleshooting

**`/car` says "no reset time is on record"**
The status line has not run yet, or your plan does not report usage. Send one ordinary
prompt, let the status line render, then try again. Check
`cat ~/.claude/rate-limit-state.json`.

**`/car` does nothing at all**
The hook is not registered. Run `/hooks` in Claude Code, or `python3 install.py` again and
restart. Confirm the interpreter path in `settings.json` still exists —
`python3 install.py --python "$(command -v python3)"` re-pins it.

**Status line went blank**
Your original command is in `~/.claude/car-inner-statusline`; run it by hand to see the
error. The recorder falls back to its own default line if yours exits non-zero with no
output.

**It waited but nothing resumed**
Look at `~/.claude/car.log`. If it shows `waiting` but never `resuming`, Claude Code hit
its hook timeout — see the `timeout` note above.

---

## Uninstall

```bash
python3 install.py --uninstall
```

Restores your previous status line, removes the hook and the three scripts, and leaves
`car.log` / `rate-limit-state.json` behind for you to delete.

---

## How it decides, and what it cannot do

You are "blocked" if either the last assistant message in the transcript is a usage-limit
API error (less than 6 hours old), or a persisted window reads ≥99% used. The reset time
comes from the maxed window — if several are maxed, the last one to reset, since that is
the one that actually unblocks you.

**It has to be two prompts.** A one-shot `/car <prompt>` cannot span a limit *in the same
session*: this hook returns before your prompt starts running, and Claude Code fires no
hook when a turn later dies on the limit (`Stop` only fires on success). Nothing can type
into a live session either. A *queued* prompt does survive that dead turn, which is
exactly what the recipe relies on.

A true one-shot is possible if you give up staying in the TUI — have the hook arm a cron
job for the reset time that starts a headless `claude --resume <session_id> -p "Continue"`.
That is deliberately not built here: the resumed work would edit files unattended, outside
the session you are watching, and a headless resume writing to a transcript whose TUI is
still open can conflict.

Nothing in here talks to the network.
