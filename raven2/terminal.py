"""Open the live Claude Code session in a real terminal window so the user can drive it by hand."""

from __future__ import annotations

import shlex
import shutil
import subprocess


def claude_command(cwd: str, session_id: str, claude_path: str | None = None) -> str:
    """Shell line that resumes the session in its own project directory. RAVEN is unset so the old V1
    hooks (which gate on it) stay quiet in the manual session."""
    exe = claude_path or shutil.which("claude")
    if not exe:
        raise FileNotFoundError("couldn't find the `claude` command on PATH")
    return f"cd {shlex.quote(cwd)} && env -u RAVEN {shlex.quote(exe)} --resume {shlex.quote(session_id)}"


def _applescript_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def script_for(cmd: str, app: str = "Terminal") -> str:
    if app.lower() == "iterm":
        return ('tell application "iTerm"\n  activate\n  set w to (create window with default profile)\n'
                f"  tell current session of w to write text {_applescript_str(cmd)}\nend tell")
    return f'tell application "Terminal"\n  activate\n  do script {_applescript_str(cmd)}\nend tell'


def open_in_terminal(cmd: str, app: str = "Terminal") -> None:
    r = subprocess.run(["osascript", "-e", script_for(cmd, app)], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(f"couldn't open {app}: {r.stderr.strip() or 'osascript failed'}")
