from __future__ import annotations

import threading

import numpy as np

from ..config import MODELS_DIR


class FasterWhisperSTT:
    def __init__(self, model: str = "base", compute_type: str = "int8", language: str | None = "en",
                 threads: int = 0, **_):
        self._name, self._ct, self._lang, self._threads = model, compute_type, language, threads
        self._model = None
        self._load_lock = threading.Lock()

    def _load(self):
        with self._load_lock:  # lazy, and safe if warmup and a real request race
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self._name, device="cpu", compute_type=self._ct,
                                           cpu_threads=self._threads, download_root=str(MODELS_DIR / "whisper"))
            return self._model

    def transcribe(self, samples: np.ndarray, sample_rate: int = 16_000) -> str:
        if sample_rate != 16_000:
            raise ValueError("faster-whisper backend expects 16 kHz audio")
        segs, _ = self._load().transcribe(samples.astype(np.float32), language=self._lang,
                                          beam_size=1, vad_filter=False)
        return " ".join(s.text for s in segs).strip()
