"""`raven-voice` — list or switch the active Raven voice from the shell."""

import sys

from .config import ELEVENLABS_VOICES
from .voices import get_active_name, match_voice_command, set_active_voice


def main() -> None:
    args = sys.argv[1:]
    if not ELEVENLABS_VOICES:
        print("No voices configured (set ELEVENLABS_VOICES in .env).", file=sys.stderr)
        sys.exit(1)
    if not args or args[0] in ("--list", "-l"):
        active = get_active_name()
        for name in ELEVENLABS_VOICES:
            print(f"{name}{' (active)' if name == active else ''}")
        return
    entry = match_voice_command("switch to " + " ".join(args), ELEVENLABS_VOICES)
    if entry is None:
        print(f"Unknown voice: {' '.join(args)}", file=sys.stderr)
        sys.exit(1)
    set_active_voice(entry.name, entry.voice_id, entry.personality, entry.speed)
    print(f"Voice switched to {entry.name}")


if __name__ == "__main__":
    main()
