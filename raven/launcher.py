"""
Raven launcher — sets RAVEN=1 in the environment, starts the push-to-talk
listen daemon, then hands off to the `claude` CLI.
"""

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .config import RAVEN_USER_NAME, RAVEN_USER_PRONOUNS
from .listen import ListenDaemon

_PROMPT_FILE = Path(__file__).parent / "prompts" / "voice_mode.md"


def _build_system_prompt() -> str:
    """Static voice-mode rules plus identity. The persona is NOT here; it is
    injected every turn by the UserPromptSubmit hook (raven.prompt)."""
    text = _PROMPT_FILE.read_text()
    identity = []
    if RAVEN_USER_NAME:
        identity.append(f"- Address the user as {RAVEN_USER_NAME}.")
    if RAVEN_USER_PRONOUNS:
        identity.append(f"- Use {RAVEN_USER_PRONOUNS} pronouns for the user.")
    if identity:
        text += "\n## User\n" + "\n".join(identity) + "\n"
    return text


def main() -> None:
    env = os.environ.copy()
    env["RAVEN"] = "1"

    # Record the current frontmost app so listen.py can refocus it before injecting
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=3,
        )
        terminal_app = result.stdout.strip()
        if terminal_app:
            with open("/tmp/raven_terminal_app", "w") as f:
                f.write(terminal_app)
            print(f"[raven] Terminal app: {terminal_app}", flush=True)
    except Exception as e:
        print(f"[raven] Could not detect terminal app: {e}", flush=True)

    # Start the push-to-talk daemon in a background thread
    daemon = ListenDaemon()
    t = threading.Thread(target=daemon.run, daemon=True)
    t.start()

    # Hand off to claude with the RAVEN system prompt injected.
    # --append-system-prompt is the reliable way to tell the model it's in voice mode
    # (env vars like RAVEN=1 are not visible to the model).
    # User-supplied args come last so --resume / --continue etc. still work.
    with tempfile.NamedTemporaryFile("w", suffix=".md", prefix="raven_prompt_", delete=False) as f:
        f.write(_build_system_prompt())
        prompt_path = f.name
    args = ["claude", "--append-system-prompt-file", prompt_path] + sys.argv[1:]
    try:
        result = subprocess.run(args, env=env)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        sys.exit(0)
    except FileNotFoundError:
        print("[raven] Error: 'claude' CLI not found. Is Claude Code installed?", file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass
