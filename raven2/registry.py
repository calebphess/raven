"""Backend registry. Backends are named in config and imported lazily, so heavy
dependencies (llama.cpp, whisper, onnx) load only when a profile selects them."""

from __future__ import annotations

import importlib
from typing import Any, Callable

KINDS = ("stt", "llm", "tts", "worker", "audio_out")

# kind -> name -> "module:Class" (lazy) or a callable (registered at runtime)
_REG: dict[str, dict[str, str | Callable[..., Any]]] = {k: {} for k in KINDS}


def register(kind: str, name: str, target: str | Callable[..., Any] | None = None):
    """register("stt", "mine", "pkg.mod:Cls") or use as `@register("stt", "mine")` on a class."""
    if kind not in _REG:
        raise ValueError(f"unknown backend kind {kind!r}; expected one of {KINDS}")
    if target is not None:
        _REG[kind][name] = target
        return target

    def deco(cls):
        _REG[kind][name] = cls
        return cls

    return deco


def create(kind: str, name: str, **params):
    try:
        target = _REG[kind][name]
    except KeyError:
        raise KeyError(f"no {kind} backend {name!r}; available: {sorted(_REG[kind])}") from None
    if isinstance(target, str):
        mod, _, attr = target.partition(":")
        target = getattr(importlib.import_module(mod), attr)
    return target(**params)


def available(kind: str) -> list[str]:
    return sorted(_REG[kind])


# Built-ins
register("stt", "faster-whisper", "raven2.backends.whisper_stt:FasterWhisperSTT")
register("llm", "llama-cpp", "raven2.backends.llamacpp_llm:LlamaCppLLM")
register("tts", "piper", "raven2.backends.piper_tts:PiperTTS")
register("audio_out", "sounddevice", "raven2.backends.sd_out:SoundDeviceOut")
register("worker", "null", "raven2.backends.null_worker:NullWorker")
register("worker", "claude", "raven2.backends.claude_worker:ClaudeWorker")
