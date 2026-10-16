"""
Claude Code PermissionRequest hook.

Speaks a short summary of what Claude wants to do, then waits for Penn to
answer with the normal push-to-talk key. The listen daemon sees the pending
marker, saves the transcript as the answer instead of typing it into the
terminal, and this hook maps it to allow / always allow / deny.

If there is no clear answer in time, the hook prints nothing and Claude Code
shows its normal permission dialog.

Does nothing unless RAVEN=1.
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from .tts import speak

RAVEN_DIR = Path.home() / ".raven"
PENDING = RAVEN_DIR / "permission_pending"
ANSWER = RAVEN_DIR / "permission_answer"
ANSWER_TIMEOUT_S = 20

_SUFFIX = ["Yes, always, or no?", "What do you say?", "Go ahead, or no?"]

_DENY = re.compile(r"\b(no|nope|deny|stop|cancel|don'?t|do not)\b")
_ALWAYS = re.compile(r"\b(always|yes to all|allow all|all of them)\b")
_ALLOW = re.compile(r"\b(yes|yeah|yep|sure|ok|okay|go ahead|do it|allow|approve)\b")


def parse_decision(text: str) -> str | None:
    """Return 'deny', 'always', 'allow', or None. Deny is checked first: a
    wrong deny just re-prompts, a wrong allow runs the command."""
    t = text.lower()
    if _DENY.search(t):
        return "deny"
    if _ALWAYS.search(t):
        return "always"
    if _ALLOW.search(t):
        return "allow"
    return None


def _basename(inp: dict) -> str:
    path = inp.get("file_path") or inp.get("path") or ""
    return os.path.basename(path) if path else "a file"


def build_speech(data: dict) -> str:
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}

    if tool == "Bash":
        desc = (inp.get("description") or "").strip()
        summary = f"Run: {desc}" if desc else "I need your approval to run a command"
    elif tool in ("Write", "Edit", "MultiEdit"):
        summary = f"{tool} {_basename(inp)}"
    elif tool == "Read":
        summary = f"Read {_basename(inp)}"
    elif tool == "WebFetch":
        url = inp.get("url", "")
        summary = f"Fetch {urlparse(url).netloc or url or 'a page'}"
    elif tool == "WebSearch":
        q = inp.get("query", "")
        summary = f"Search: {q[:60]}"
    else:
        summary = f"Use {tool}" if tool else "Permission needed"
    return f"{summary}. {random.choice(_SUFFIX)}"


def _wait_for_answer() -> str | None:
    deadline = time.monotonic() + ANSWER_TIMEOUT_S
    while time.monotonic() < deadline:
        if ANSWER.exists():
            try:
                text = ANSWER.read_text()
            finally:
                ANSWER.unlink(missing_ok=True)
            return text
        time.sleep(0.1)
    return None


def main() -> None:
    if os.environ.get("RAVEN") != "1":
        sys.exit(0)
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        sys.exit(0)

    RAVEN_DIR.mkdir(exist_ok=True)
    ANSWER.unlink(missing_ok=True)

    speak(build_speech(data), block=True)

    # Mark pending only after the alert finishes so the daemon does not
    # capture speech meant for something else.
    PENDING.write_text(str(data.get("session_id", "")))
    try:
        heard = _wait_for_answer()
    finally:
        PENDING.unlink(missing_ok=True)

    if heard is None:
        print("[raven] No answer in time; showing normal dialog.", file=sys.stderr)
        sys.exit(0)

    print(f"[raven] Heard: {heard!r}", file=sys.stderr)
    decision = parse_decision(heard)
    if decision is None:
        sys.exit(0)

    result: dict = {"behavior": "deny" if decision == "deny" else "allow"}
    if decision == "deny":
        result["message"] = "Denied by voice."
    elif decision == "always" and data.get("permission_suggestions"):
        result["updatedPermissions"] = data["permission_suggestions"]

    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": result}
    }))


if __name__ == "__main__":
    main()
