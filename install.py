#!/usr/bin/env python3
"""
Installer for the /car Claude Code command ("continue after reset").

  python3 install.py              install
  python3 install.py --dry-run    show what would change, touch nothing
  python3 install.py --uninstall  remove it and restore the previous status line
  python3 install.py --python /usr/bin/python3   pin the interpreter used by the hook

Works on macOS, Linux, WSL and Windows.  Needs Python 3.7+ and nothing else.
settings.json is backed up before it is edited.
"""

import argparse
import json
import os
import shutil
import stat
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "files")

SCRIPTS = ["car_wait.py", "car_usage_recorder.py"]
HOOK_TIMEOUT = 30000            # seconds; must exceed the longest wait /car will do (8h)


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")


def quote(p):
    return '"%s"' % p if " " in p else p


def pick_python():
    """A stable interpreter path for the hook command.

    sys.executable is guaranteed to work but is often version-pinned
    (.../python@3.14/bin/python3.14), which breaks the hook the next time the friend
    upgrades Python.  Prefer a plain `python3` off PATH when it is new enough.
    """
    import subprocess
    names = ("python", "python3") if os.name == "nt" else ("python3", "python")
    for name in names:
        p = shutil.which(name)
        if not p:
            continue
        try:
            r = subprocess.run(
                [p, "-c", "import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        except Exception:
            continue
        if r.returncode == 0:
            return p
    return sys.executable


def load_settings(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            text = fh.read().strip()
        return json.loads(text) if text else {}
    except ValueError as e:
        sys.exit("ERROR: %s is not valid JSON (%s). Fix or move it, then re-run." %
                 (path, e))


def save_settings(path, data, dry):
    if dry:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def backup(path, dry):
    if not os.path.exists(path):
        return None
    dest = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    if not dry:
        shutil.copy2(path, dest)
    return dest


def hook_entries(settings):
    return settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])


def find_car_hook(settings):
    for group in settings.get("hooks", {}).get("UserPromptSubmit", []):
        for h in group.get("hooks", []):
            cmd = h.get("command", "")
            if "car_wait" in cmd or "car-wait" in cmd:
                return group, h
    return None, None


def install(args):
    cfg = config_dir()
    commands = os.path.join(cfg, "commands")
    settings_path = os.path.join(cfg, "settings.json")
    dry = args.dry_run
    log = []

    python = args.python or pick_python()
    if not python:
        sys.exit("ERROR: could not determine a Python interpreter; pass --python PATH")

    # 1. Files ---------------------------------------------------------------------
    if not dry:
        os.makedirs(commands, exist_ok=True)
    for name in SCRIPTS:
        dest = os.path.join(cfg, name)
        if not dry:
            shutil.copy2(os.path.join(SRC, name), dest)
            os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR)
        log.append("  %s" % dest)
    cmd_dest = os.path.join(commands, "car.md")
    if os.path.exists(cmd_dest) and not dry:
        backup(cmd_dest, dry)
    if not dry:
        shutil.copy2(os.path.join(SRC, "car.md"), cmd_dest)
    log.append("  %s" % cmd_dest)

    # 2. settings.json -------------------------------------------------------------
    settings = load_settings(settings_path)
    bak = backup(settings_path, dry)
    changes = []

    hook_cmd = "%s %s" % (quote(python), quote(os.path.join(cfg, "car_wait.py")))
    group, existing = find_car_hook(settings)
    if existing:
        if existing.get("command") != hook_cmd:
            existing["command"] = hook_cmd
            existing["timeout"] = HOOK_TIMEOUT
            changes.append("updated the existing UserPromptSubmit hook")
        else:
            changes.append("UserPromptSubmit hook already registered")
    else:
        hook_entries(settings).append(
            {"hooks": [{"type": "command", "command": hook_cmd,
                        "timeout": HOOK_TIMEOUT}]})
        changes.append("registered the UserPromptSubmit hook")

    recorder = "%s %s" % (quote(python),
                          quote(os.path.join(cfg, "car_usage_recorder.py")))
    sl = settings.get("statusLine") or {}
    current = sl.get("command", "") if sl.get("type") == "command" else ""
    inner_path = os.path.join(cfg, "car-inner-statusline")

    if "car_usage_recorder" in current:
        changes.append("status line already routed through the recorder")
    else:
        if current:
            if not dry:
                with open(inner_path, "w") as fh:
                    fh.write("# Your original Claude Code status-line command.\n"
                             "# car_usage_recorder.py runs it and prints its output "
                             "verbatim.\n"
                             "# Delete this file to fall back to the built-in "
                             "compact line.\n"
                             "%s\n" % current)
            changes.append("saved your existing status line to car-inner-statusline "
                           "and chained it behind the recorder")
        else:
            changes.append("set a status line (none was configured) so usage can be "
                           "recorded")
        settings["statusLine"] = {"type": "command", "command": recorder}

    save_settings(settings_path, settings, dry)

    # 3. Report --------------------------------------------------------------------
    print("%s/car -> %s" % ("[dry run] " if dry else "", cfg))
    print("\nFiles:")
    print("\n".join(log))
    print("\nsettings.json (%s):" % settings_path)
    for c in changes:
        print("  - %s" % c)
    if bak:
        print("  - backup: %s" % bak)
    print("\nInterpreter used by the hook: %s" % python)
    if dry:
        print("\nNothing was written. Drop --dry-run to apply.")
        return
    print("""
Restart Claude Code, then check it is live:
  /hooks          -> car_wait.py should appear under UserPromptSubmit
  /car            -> "the limit was never hit - nothing to continue" (that is a PASS)

Everyday use: send your long prompt, then immediately type  /car  and hit enter so it
queues behind it. If the usage limit kills the turn, /car sleeps until the reset and
resumes the work. If the turn finishes fine, /car sends nothing and costs nothing.""")


def uninstall(args):
    cfg = config_dir()
    settings_path = os.path.join(cfg, "settings.json")
    dry = args.dry_run
    settings = load_settings(settings_path)
    bak = backup(settings_path, dry)
    removed = []

    group, existing = find_car_hook(settings)
    if existing:
        group["hooks"].remove(existing)
        if not group["hooks"]:
            settings["hooks"]["UserPromptSubmit"].remove(group)
        if not settings["hooks"].get("UserPromptSubmit"):
            settings["hooks"].pop("UserPromptSubmit", None)
        if not settings.get("hooks"):
            settings.pop("hooks", None)
        removed.append("UserPromptSubmit hook")

    sl = settings.get("statusLine") or {}
    if "car_usage_recorder" in (sl.get("command") or ""):
        inner_path = os.path.join(cfg, "car-inner-statusline")
        inner = ""
        try:
            with open(inner_path) as fh:
                inner = next((ln.strip() for ln in fh
                              if ln.strip() and not ln.lstrip().startswith("#")), "")
        except OSError:
            pass
        if inner:
            settings["statusLine"] = {"type": "command", "command": inner}
            removed.append("status line restored to: %s" % inner)
        else:
            settings.pop("statusLine", None)
            removed.append("status line (removed)")
        if not dry:
            try:
                os.remove(inner_path)
            except OSError:
                pass

    save_settings(settings_path, settings, dry)

    for p in [os.path.join(cfg, n) for n in SCRIPTS] + \
             [os.path.join(cfg, "commands", "car.md")]:
        if os.path.exists(p):
            if not dry:
                os.remove(p)
            removed.append(p)

    print("%sUninstalled /car." % ("[dry run] " if dry else ""))
    for r in removed:
        print("  - %s" % r)
    if bak:
        print("  - settings backup: %s" % bak)
    print("\n%s/car.log and %s/rate-limit-state.json were left in place; "
          "delete them if you want." % (cfg, cfg))


def main():
    p = argparse.ArgumentParser(description="Install the /car Claude Code command.")
    p.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    p.add_argument("--uninstall", action="store_true", help="remove /car")
    p.add_argument("--python", help="interpreter the hook should run under")
    args = p.parse_args()

    if sys.version_info < (3, 7):
        sys.exit("ERROR: Python 3.7+ required (found %s)." %
                 ".".join(map(str, sys.version_info[:3])))
    for name in SCRIPTS + ["car.md"]:
        if not os.path.exists(os.path.join(SRC, name)):
            sys.exit("ERROR: missing %s - run this from inside the unpacked folder."
                     % os.path.join(SRC, name))

    uninstall(args) if args.uninstall else install(args)


if __name__ == "__main__":
    main()
