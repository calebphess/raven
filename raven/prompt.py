"""
Claude Code UserPromptSubmit hook (must be synchronous, not async).

1. Fires a "thinking" phrase in the background so Penn knows the prompt landed.
2. Prints the active voice persona as additionalContext so <raven> text is
   written in that voice on this very turn.

Does nothing unless RAVEN=1.
"""

import json
import os
import random
import subprocess
import sys

PHRASES = [
    "Hmm, let me think about that.",
    "Let me take a look.",
    "On it.",
    "Sure, give me a moment.",
    "Let me check that.",
]


def main() -> None:
    if os.environ.get("RAVEN") != "1":
        sys.exit(0)

    from .voices import get_active_name, get_active_personality

    subprocess.Popen(
        [sys.executable, "-m", "raven.tts", random.choice(PHRASES)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    name, personality = get_active_name(), get_active_personality()
    if name and personality:
        context = (
            f"Raven voice persona for this turn: {name}. Write everything inside "
            f"<raven> tags in this voice: {personality} This overrides any earlier persona."
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }))


if __name__ == "__main__":
    main()
