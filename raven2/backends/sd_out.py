from __future__ import annotations

import threading

from math import gcd

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly

from ..interfaces import Audio


class SoundDeviceOut:
    """One persistent output stream fed from a buffer: no per-sentence stream reopen (which clicks/gaps),
    instant cancel for barge-in. The stream runs at the device's native rate and we resample ourselves;
    leaving it to CoreAudio (e.g. 22.05 kHz -> 48 kHz on AirPods) crackled."""

    def __init__(self, **_):
        self._buf = np.zeros(0, np.float32)
        self._lock = threading.Lock()
        self._empty = threading.Event()
        self._empty.set()
        self._stream: sd.OutputStream | None = None
        self._rate = int(sd.query_devices(kind="output")["default_samplerate"])

    def _callback(self, outdata, frames, _time, _status):
        with self._lock:
            n = min(frames, len(self._buf))
            outdata[:n, 0] = self._buf[:n]
            outdata[n:, 0] = 0
            self._buf = self._buf[n:]
            if len(self._buf) == 0:
                self._empty.set()

    def _ensure(self) -> None:
        if self._stream is None:
            self._stream = sd.OutputStream(samplerate=self._rate, channels=1, dtype="float32",
                                           latency="high", callback=self._callback)
            self._stream.start()

    def play(self, audio: Audio) -> None:
        x = audio.samples.astype(np.float32)
        if audio.sample_rate != self._rate:
            g = gcd(self._rate, audio.sample_rate)
            x = resample_poly(x, self._rate // g, audio.sample_rate // g).astype(np.float32)
        self._ensure()
        with self._lock:
            self._buf = np.concatenate([self._buf, x])
            self._empty.clear()

    def cancel(self) -> None:
        with self._lock:
            self._buf = np.zeros(0, np.float32)
            self._empty.set()

    def wait(self) -> None:
        self._empty.wait()
        if self._stream is not None:
            threading.Event().wait(0.15)  # let the device drain its last block
