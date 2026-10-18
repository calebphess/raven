from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Iterator

from ..config import resolve_model


def default_threads() -> int:
    """Physical performance cores, not hardware threads. llama.cpp's threads spin-wait, so using every
    hyperthread while Claude runs alongside made the model ~2x slower under load than using 4 threads
    (measured on an i5, 8 threads: 4.2 s median ack under load vs 1.9 s with 4)."""
    if sys.platform == "darwin":
        for key in ("hw.perflevel0.physicalcpu", "hw.physicalcpu"):
            try:
                n = int(subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=3).stdout)
                if n > 0:
                    return n
            except Exception:
                pass
    return max(1, (os.cpu_count() or 2) // 2)


class LlamaCppLLM:
    def __init__(self, model: str, n_ctx: int = 2048, n_threads: int | None = None,
                 n_gpu_layers: int = 0, temperature: float = 0.7, cache_mb: int = 768, **_):
        self._path = resolve_model(model)
        self._kw = dict(n_ctx=n_ctx, n_threads=n_threads or default_threads(),
                        n_gpu_layers=n_gpu_layers, verbose=False)
        self._temp = temperature
        self._cache_mb = cache_mb
        self._llm = None
        self._load_lock = threading.Lock()
        self._gen_lock = threading.Lock()  # llama.cpp contexts are not thread-safe; callers take turns

    def _load(self):
        with self._load_lock:
            if self._llm is None:
                from llama_cpp import Llama

                self._llm = Llama(model_path=str(self._path), **self._kw)
                if self._cache_mb:
                    # Raven alternates between several prompt types (ack, small talk, progress, relay). With one
                    # KV state, each switch re-reads a whole prompt (3-6 s on this CPU); the state cache keeps
                    # one per prompt prefix, so warmed prompts start in about a second.
                    from llama_cpp import LlamaRAMCache

                    self._llm.set_cache(LlamaRAMCache(capacity_bytes=self._cache_mb << 20))
            return self._llm

    def stream(self, messages: list[dict], max_tokens: int = 160, temperature: float | None = None) -> Iterator[str]:
        with self._gen_lock:
            gen = self._load().create_chat_completion(messages=messages, max_tokens=max_tokens,
                                                      temperature=self._temp if temperature is None else temperature,
                                                      stream=True)
            try:
                for chunk in gen:
                    piece = chunk["choices"][0]["delta"].get("content")
                    if piece:
                        yield piece
            finally:
                gen.close()
