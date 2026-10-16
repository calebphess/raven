"""
Raven installer.

Installs four Claude Code hooks into ~/.claude/settings.json (all of them are
no-ops unless the session was started with `raven`, which sets RAVEN=1):

  UserPromptSubmit   raven.prompt      persona + thinking phrase (synchronous)
  PreToolUse         raven.speak       speaks the plan before tools run
  Stop               raven.speak       speaks the final synopsis
  PermissionRequest  raven.permission  voice approval

Also removes the old "Raven Voice Mode" section from ~/.claude/CLAUDE.md; voice
instructions now come only from the launcher.

Usage: python install.py [--uninstall]
"""

import json
import re
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"

HOOKS = {
    "UserPromptSubmit": "raven.prompt",
    "PreToolUse": "raven.speak",
    "Stop": "raven.speak",
    "PermissionRequest": "raven.permission",
}


def _is_raven(command: str) -> bool:
    return " -m raven." in command or "raven-speak" in command


def _load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except json.JSONDecodeError:
        sys.exit(f"{SETTINGS_FILE} is not valid JSON; fix it first.")


def _strip_raven_hooks(settings: dict) -> None:
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        kept = []
        for entry in hooks[event]:
            entry["hooks"] = [h for h in entry.get("hooks", []) if not _is_raven(h.get("command", ""))]
            if entry["hooks"]:
                kept.append(entry)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]


def install_hooks() -> None:
    CLAUDE_DIR.mkdir(exist_ok=True)
    settings = _load_settings()
    _strip_raven_hooks(settings)
    hooks = settings.setdefault("hooks", {})
    for event, module in HOOKS.items():
        command = f"{sys.executable} -m {module}"
        hooks.setdefault(event, []).append(
            {"matcher": "", "hooks": [{"type": "command", "command": command}]}
        )
        print(f"  ✓ {event} → {command}")
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


def uninstall_hooks() -> None:
    settings = _load_settings()
    _strip_raven_hooks(settings)
    if not settings.get("hooks"):
        settings.pop("hooks", None)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    print("  ✓ Raven hooks removed")


def remove_claude_md_section() -> None:
    if not CLAUDE_MD.exists():
        return
    text = CLAUDE_MD.read_text()
    new = re.sub(r"\n*## Raven Voice Mode\n.*?(?=\n## |\Z)", "\n", text, flags=re.DOTALL)
    if new != text:
        CLAUDE_MD.write_text(new.rstrip() + "\n")
        print(f"  ✓ Removed old Raven section from {CLAUDE_MD}")
    else:
        print("  ✓ No Raven section in CLAUDE.md")


def main() -> None:
    if "--uninstall" in sys.argv:
        print("Uninstalling Raven…\n")
        uninstall_hooks()
        remove_claude_md_section()
        return
    print("Installing Raven…\n")
    install_hooks()
    remove_claude_md_section()
    print("\nDone! Run `raven` to start Claude Code with voice.\nHold Right ⌥ Option to speak, release to send.")


if __name__ == "__main__":
    main()
