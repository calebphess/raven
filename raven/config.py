import os
from pathlib import Path
from dotenv import load_dotenv

# Credentials/config come from, in priority order:
#   1. real environment variables
#   2. ~/.raven/.env (works for non-editable installs)
#   3. .env in the project root
# load_dotenv never overrides variables that are already set.
load_dotenv(Path.home() / ".raven" / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Identity ──────────────────────────────────────────────────────────────────
RAVEN_USER_NAME: str = os.getenv("RAVEN_USER_NAME", "")
RAVEN_USER_PRONOUNS: str = os.getenv("RAVEN_USER_PRONOUNS", "")

# ── ElevenLabs ────────────────────────────────────────────────────────────────
ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL: str = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2")
ELEVENLABS_SPEED: float = float(os.getenv("ELEVENLABS_SPEED", "1.3"))

# Named voice list with optional personality descriptions.
# Format: "Name:voiceId:personality text|Name:voiceId:personality text"
# Keys are the names you say aloud ("switch to Carter").
from dataclasses import dataclass

@dataclass
class VoiceEntry:
    name: str
    voice_id: str
    speed: float = 1.0
    personality: str = ""

def _parse_voices(raw: str) -> dict[str, VoiceEntry]:
    voices: dict[str, VoiceEntry] = {}
    for entry in raw.split("|"):
        entry = entry.strip()
        if not entry:
            continue
        # Format: Name:voiceId:speed:personality (speed is optional, defaults to 1.0)
        parts = entry.split(":", 3)
        if len(parts) >= 2:
            name = parts[0].strip()
            vid = parts[1].strip()
            speed = 1.0
            personality = ""
            if len(parts) >= 3:
                try:
                    speed = float(parts[2].strip())
                    personality = parts[3].strip() if len(parts) == 4 else ""
                except ValueError:
                    # parts[2] is not a number — treat as personality (old format)
                    personality = parts[2].strip()
            voices[name] = VoiceEntry(name=name, voice_id=vid, speed=speed, personality=personality)
    return voices

ELEVENLABS_VOICES: dict[str, VoiceEntry] = _parse_voices(os.getenv("ELEVENLABS_VOICES", ""))

WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "base")

SAMPLE_RATE: int = 16_000
MIN_RECORDING_SECONDS: float = 0.3  # ignore accidental taps

# XML tag used in Claude's response to mark text that should be spoken aloud
RAVEN_TAG: str = "raven"
