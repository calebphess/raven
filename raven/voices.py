"""
Voice management — maintains the active ElevenLabs voice in a state file
so all TTS callers (speak, permission, thinking) stay in sync, and so the
active personality can be injected into Claude's context each turn.
"""

import json
import re
from pathlib import Path

_STATE_FILE = Path.home() / ".raven_active_voice"


# ── State I/O ──────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.write_text(json.dumps(state))
    except Exception:
        pass


def get_active_voice_id(default: str) -> str:
    return _load_state().get("voice_id", default)


def get_active_personality() -> str:
    return _load_state().get("personality", "")


def get_active_name() -> str:
    return _load_state().get("name", "")


def get_active_speed(default: float) -> float:
    return float(_load_state().get("speed", default))


def set_active_voice(name: str, voice_id: str, personality: str, speed: float = 1.0) -> None:
    _save_state({"name": name, "voice_id": voice_id, "personality": personality, "speed": speed})


# ── Voice command matching ─────────────────────────────────────────────────────

def match_list_command(text: str) -> bool:
    """Return True if text is a request to list available voices."""
    t = text.strip().lower().rstrip(".")
    patterns = [
        "list voices",
        "list available voices",
        "what voices",
        "show voices",
        "which voices",
        "available voices",
    ]
    return any(p in t for p in patterns)


def match_voice_command(text: str, voices: dict) -> "VoiceEntry | None":
    """
    If text is a voice-switch command, return the matching VoiceEntry.
    Otherwise return None.

    Recognised patterns (case-insensitive):
      switch to <name>
      switch voice to <name>
      use <name> [voice]
      change [voice] to <name>
    """
    t = text.strip().lower().rstrip(".")

    patterns = [
        r"^switch(?:\s+voice)?\s+to\s+(.+)$",
        r"^use\s+(.+?)(?:\s+voice)?$",
        r"^change(?:\s+voice)?\s+to\s+(.+)$",
    ]

    for pattern in patterns:
        m = re.match(pattern, t)
        if m:
            spoken_name = m.group(1).strip()
            for name, entry in voices.items():
                if spoken_name == name.lower() or spoken_name in name.lower() or name.lower() in spoken_name:
                    return entry
    return None
