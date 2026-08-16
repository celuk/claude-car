# Let Claude install it for you

If you would rather not run the installer yourself, open Claude Code **in this unpacked
folder** and paste the prompt below.

---

```
This folder contains a custom Claude Code slash command called /car and an installer for
it. Please set it up in my Claude Code environment.

1. Read README.md and install.py so you know what it does before changing anything.
2. Run `python3 install.py --dry-run` and tell me exactly what it will change in my
   ~/.claude/settings.json — especially whether I already have a status line or other
   UserPromptSubmit hooks that need preserving.
3. If it looks right, run `python3 install.py` for real.
4. Verify: confirm the hook is registered in settings.json, confirm the three files landed
   (~/.claude/commands/car.md, ~/.claude/car_wait.py, ~/.claude/car_usage_recorder.py),
   and prove the hook logic runs by piping a test payload into it:
     echo '{"prompt":"/car","transcript_path":""}' | python3 ~/.claude/car_wait.py
   On a healthy account that should print "the limit was never hit" and exit 2. That is
   the expected pass, not an error.
5. Then explain to me in a few lines how to actually use it day to day.

Do not edit my settings.json by hand — the installer merges and backs it up. If something
about my setup makes the installer the wrong move, stop and tell me instead of improvising.
```

---

## If you would rather it were explained first

```
Read README.md in this folder and explain what /car does, what it will change in my
Claude Code config, and whether there is any downside to installing it. Do not install
anything yet.
```

## Afterwards

Restart Claude Code, then type `/car`. On a healthy account you should see:

```
/car: the limit was never hit - nothing to continue, so nothing was sent.
```

That is the success case — it means the hook ran and correctly refused to spend anything.

Then use it like this: send a long prompt, and while it is still running type `/car` and
hit enter so it queues behind. If you hit the limit, `/car` waits out the reset and
continues the work. If you don't, it sends nothing.
