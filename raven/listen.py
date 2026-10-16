"""
Push-to-talk listener daemon.

Hold Right Option (⌥) to record. Release to transcribe with faster-whisper,
then either type the text into the terminal or, if a permission request is
pending (see permission.py), hand it to the permission hook as the answer.

Requires:
  - Microphone permission (System Settings → Privacy → Microphone)
  - Input Monitoring permission (System Settings → Privacy → Input Monitoring)
"""

import subprocess
import threading
from pathlib import Path
import time

import numpy as np
import pyperclip
import sounddevice as sd
from faster_whisper import WhisperModel
from pynput import keyboard as kb_module
from pynput.keyboard import Controller, Key

from .config import ELEVENLABS_VOICES, MIN_RECORDING_SECONDS, SAMPLE_RATE, WHISPER_MODEL
from .tts import interrupt, speak
from .voices import match_list_command, match_voice_command, set_active_voice

_RAVEN_DIR = Path.home() / ".raven"
_PENDING = _RAVEN_DIR / "permission_pending"
_ANSWER = _RAVEN_DIR / "permission_answer"


def _permission_pending() -> bool:
    """A pending marker older than 30s is stale (hook was killed)."""
    try:
        return time.time() - _PENDING.stat().st_mtime < 30
    except OSError:
        return False

_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")


class ListenDaemon:
    """
    Runs a push-to-talk loop using the Right Option key.
    Injects transcribed text as keypresses into the focused window.
    """

    def __init__(self):
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._record_start: float = 0.0
        self._kb = Controller()

    # ------------------------------------------------------------------
    # Audio callback
    # ------------------------------------------------------------------

    def _audio_callback(self, indata, _frames, _time, _status):
        if self._recording:
            self._frames.append(indata.copy())

    # ------------------------------------------------------------------
    # Recording control
    # ------------------------------------------------------------------

    def _start_recording(self):
        # Interrupt any currently playing audio
        interrupt()
        self._frames = []
        self._record_start = time.monotonic()
        self._recording = True
        print("[raven] Recording…", flush=True)

    def _stop_and_transcribe(self):
        self._recording = False
        duration = time.monotonic() - self._record_start

        if duration < MIN_RECORDING_SECONDS or not self._frames:
            return

        print("[raven] Transcribing…", flush=True)

        audio = np.concatenate(self._frames, axis=0).flatten()

        try:
            segments, _ = _model.transcribe(audio, language="en")
            text = " ".join(s.text for s in segments).strip()
        except Exception as e:
            print(f"[raven] Transcription error: {e}", flush=True)
            return

        if text:
            print(f"[raven] → {text}", flush=True)
            if _permission_pending():
                _ANSWER.write_text(text)
                return
            if ELEVENLABS_VOICES and self._handle_voice_command(text):
                return
            self._inject_text(text)

    # ------------------------------------------------------------------
    # Voice switching
    # ------------------------------------------------------------------

    def _handle_voice_command(self, text: str) -> bool:
        """Check if text is a voice command (switch or list). Handle if so."""
        if match_list_command(text):
            names = list(ELEVENLABS_VOICES.keys())
            speech = "Available voices: " + ", ".join(names) + "."
            print(f"[raven] {speech}", flush=True)
            self._speak_confirmation(speech)
            return True

        entry = match_voice_command(text, ELEVENLABS_VOICES)
        if entry is None:
            return False

        set_active_voice(entry.name, entry.voice_id, entry.personality, entry.speed)
        print(f"[raven] Voice switched to {entry.name}", flush=True)
        self._speak_confirmation(f"Switched to {entry.name}.")
        return True

    def _speak_confirmation(self, text: str) -> None:
        try:
            speak(text, block=True)
        except Exception as e:
            print(f"[raven] Confirmation error: {e}", flush=True)

    # ------------------------------------------------------------------
    # Text injection
    # ------------------------------------------------------------------

    def _focus_terminal(self):
        """Bring the recorded terminal app back to the foreground."""
        try:
            with open("/tmp/raven_terminal_app") as f:
                app = f.read().strip()
            if app:
                subprocess.run(
                    ["osascript", "-e", f'tell application "{app}" to activate'],
                    capture_output=True, timeout=3,
                )
                time.sleep(0.25)  # give the window time to come forward
        except Exception:
            pass

    def _inject_text(self, text: str):
        """Refocus the terminal, paste text from clipboard, press Enter."""
        print("[raven] Injecting text…", flush=True)
        self._focus_terminal()
        pyperclip.copy(text)
        time.sleep(0.15)
        with self._kb.pressed(Key.cmd):
            self._kb.tap("v")
        time.sleep(0.3)
        self._kb.tap(Key.enter)
        print("[raven] Injected.", flush=True)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        print("[raven] Ready. Hold Right ⌥ Option to speak.", flush=True)

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype=np.float32,
            callback=self._audio_callback,
        ):
            def on_press(key):
                if key == Key.alt_r and not self._recording:
                    self._start_recording()

            def on_release(key):
                if key == Key.alt_r and self._recording:
                    threading.Thread(
                        target=self._stop_and_transcribe, daemon=True
                    ).start()

            with kb_module.Listener(on_press=on_press, on_release=on_release) as listener:
                listener.join()
