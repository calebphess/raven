from __future__ import annotations

import threading

import os
from typing import Iterator

from ..config import resolve_model


class LlamaCppLLM:
    def __init__(self, model: str, n_ctx: int = 2048, n_threads: int | None = None,
                 n_gpu_layers: int = 0, temperature: float = 0.7, **_):
        self._path = resolve_model(model)
        self._kw = dict(n_ctx=n_ctx, n_threads=n_threads or os.cpu_count() or 4,
                        n_gpu_layers=n_gpu_layers, verbose=False)
        self._temp = temperature
        self._llm = None
        self._load_lock = threading.Lock()
        self._gen_lock = threading.Lock()  # llama.cpp contexts are not thread-safe; callers take turns

    def _load(self):
        with self._load_lock:
            if self._llm is None:
                from llama_cpp import Llama

                self._llm = Llama(model_path=str(self._path), **self._kw)
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
