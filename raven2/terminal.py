"""Open the live Claude Code session in a real terminal window so the user can drive it by hand."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass


@dataclass
class Handle:
    """The terminal window we opened, so hand-back can close exactly that window."""
    app: str
    window_id: str
    tty: str = ""


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
    """Opens a window running `cmd` and returns "<window id>|<tty>" so it can be closed later."""
    if app.lower() == "iterm":
        return ('tell application "iTerm"\n  activate\n  set w to (create window with default profile)\n'
                f"  tell current session of w to write text {_applescript_str(cmd)}\n"
                '  return ((id of w) as text) & "|" & (tty of current session of w)\nend tell')
    return ('tell application "Terminal"\n  activate\n'
            f"  set t to do script {_applescript_str(cmd)}\n"
            '  return ((id of front window) as text) & "|" & (tty of t)\nend tell')


def close_script_for(handle: Handle) -> str:
    if handle.app.lower() == "iterm":
        return (f'tell application "iTerm"\n  try\n    close (first window whose id is {int(handle.window_id)})\n'
                "  end try\nend tell")
    return (f'tell application "Terminal"\n  try\n    close (first window whose id is {int(handle.window_id)})\n'
            "  end try\nend tell")


def open_in_terminal(cmd: str, app: str = "Terminal") -> Handle:
    r = subprocess.run(["osascript", "-e", script_for(cmd, app)], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(f"couldn't open {app}: {r.stderr.strip() or 'osascript failed'}")
    wid, _, tty = r.stdout.strip().partition("|")
    return Handle(app, wid if wid.isdigit() else "", tty)


def session_pids(session_id: str) -> list[int]:
    """Processes running `claude --resume <session_id>` as a human terminal session. The SDK worker's own
    process (stream-json) is excluded; this must only ever find the manual one."""
    out = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        if (f"--resume {session_id}" in command and "stream-json" not in command
                and pid.isdigit() and int(pid) != os.getpid()):
            pids.append(int(pid))
    return pids


def _alive(pid: int) -> bool:
    """Running, as far as we care. A zombie (exited, waiting to be reaped by its parent) is gone: signal 0
    still succeeds on it, so check the process state too."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def stop_session(session_id: str, timeout: float = 8.0) -> None:
    """End the hand-driven Claude process so only one writer ever touches the session. SIGTERM first (it
    exits cleanly and flushes the transcript), SIGKILL if it won't go. Raises if it is still running, in
    which case the caller must NOT resume the session."""
    pids = session_pids(session_id)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.1)
    for pid in [p for p in pids if _alive(p)]:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(0.3)
    left = [p for p in pids if _alive(p)]
    if left:
        raise RuntimeError("the Claude session is still running in the terminal; close it yourself, then hand back")


def close_window(handle: Handle | None) -> None:
    """Close the terminal window we opened. Best effort: a window the user already closed is fine."""
    if handle is None or not handle.window_id:
        return
    subprocess.run(["osascript", "-e", close_script_for(handle)], capture_output=True, text=True, timeout=20)
