"""Push-to-talk: hold Right Option to record, release to send. Global hotkey, no window needed."""

from __future__ import annotations

import threading
import time

import numpy as np
import sounddevice as sd

from .core import Assistant

SAMPLE_RATE = 16_000
MIN_SECONDS = 0.3


def run(assistant: Assistant) -> None:
    from pynput import keyboard
    from pynput.keyboard import Key

    frames: list[np.ndarray] = []
    state = {"rec": False, "t0": 0.0}

    def cb(indata, *_):
        if state["rec"]:
            frames.append(indata.copy())

    def stop():
        state["rec"] = False
        if time.monotonic() - state["t0"] < MIN_SECONDS or not frames:
            return
        audio = np.concatenate(frames).flatten()
        threading.Thread(target=assistant.handle_audio, args=(audio,), daemon=True).start()

    def on_press(key):
        if key == Key.alt_r and not state["rec"]:
            assistant.barge_in()
            frames.clear()
            state.update(rec=True, t0=time.monotonic())

    def on_release(key):
        if key == Key.alt_r and state["rec"]:
            stop()

    print(f"[raven] {assistant.persona.name} ready. Hold Right ⌥ Option to talk. Ctrl+C to quit.", flush=True)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype=np.float32, callback=cb):
        with keyboard.Listener(on_press=on_press, on_release=on_release) as l:
            l.join()
