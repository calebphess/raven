"""Single text-to-speech entry point used by every Raven component."""

import fcntl
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import ELEVENLABS_API_KEY, ELEVENLABS_MODEL, ELEVENLABS_SPEED, ELEVENLABS_VOICE_ID
from .voices import get_active_speed, get_active_voice_id

_STATE_DIR = Path.home() / ".raven"
_LOCK_FILE = _STATE_DIR / "playback.lock"
_INTERRUPT_FILE = _STATE_DIR / "interrupt"


def interrupt() -> None:
    """Stop current playback and drop anything already queued behind it."""
    try:
        _STATE_DIR.mkdir(exist_ok=True)
        _INTERRUPT_FILE.write_text(str(time.time()))
    except OSError:
        pass
    subprocess.run(["pkill", "-x", "afplay"], capture_output=True)


def _interrupted_since(enqueued: float) -> bool:
    try:
        return float(_INTERRUPT_FILE.read_text()) > enqueued
    except (OSError, ValueError):
        return False


def _play_queued(path: str, speed: str, enqueued: float) -> None:
    """Wait for any earlier clip to finish, then play this one and delete it."""
    try:
        _STATE_DIR.mkdir(exist_ok=True)
        with open(_LOCK_FILE, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # released when the file closes
            if not _interrupted_since(enqueued):
                subprocess.run(["afplay", "-r", speed, path], check=False)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def speak(text: str, *, block: bool = False) -> None:
    """Speak text with the active voice. No-op without an API key or text."""
    text = (text or "").strip()
    if not text or not ELEVENLABS_API_KEY:
        return

    from elevenlabs import ElevenLabs

    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    chunks = client.text_to_speech.convert(
        voice_id=get_active_voice_id(ELEVENLABS_VOICE_ID),
        text=text,
        model_id=ELEVENLABS_MODEL,
        output_format="mp3_44100_128",
    )
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        for chunk in chunks:
            f.write(chunk)
        path = f.name

    speed = str(get_active_speed(ELEVENLABS_SPEED))
    enqueued = time.time()
    if block:
        _play_queued(path, speed, enqueued)
        return

    # Detached process queues behind any clip still playing, so hook
    # invocations never talk over each other and cleanup survives hook exit.
    subprocess.Popen(
        [sys.executable, "-m", "raven.tts", "--play", path, speed, str(enqueued)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--play":
        _play_queued(sys.argv[2], sys.argv[3], float(sys.argv[4]))
    else:
        # `python -m raven.tts "text"` — speaks and waits for playback to finish.
        speak(" ".join(sys.argv[1:]), block=True)
