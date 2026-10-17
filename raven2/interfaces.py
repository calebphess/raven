"""Ports the core depends on. Backends implement these; the core never imports a concrete backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterator, Protocol, runtime_checkable

import numpy as np


class AssetMissing(Exception):
    """A backend needs a model/voice that isn't installed; the persona pack provides it."""


@dataclass
class VoiceSpec:
    """Backend-neutral description of how a persona sounds. `params` is backend-specific."""

    backend: str
    model: str
    params: dict = field(default_factory=dict)
    effects: dict = field(default_factory=dict)


@dataclass
class Audio:
    samples: np.ndarray  # float32 mono, -1..1
    sample_rate: int


@runtime_checkable
class STT(Protocol):
    def transcribe(self, samples: np.ndarray, sample_rate: int = 16_000) -> str: ...


@runtime_checkable
class LLM(Protocol):
    def stream(self, messages: list[dict], max_tokens: int = 160, temperature: float | None = None) -> Iterator[str]:
        """Yield text pieces. Closing the generator must cancel generation."""
        ...


@runtime_checkable
class TTS(Protocol):
    def synthesize(self, text: str, voice: VoiceSpec) -> Iterator[Audio]:
        """Yield audio as it is produced so playback can start early."""
        ...


@dataclass
class WorkerEvent:
    """kind: text (assistant prose) | tool (a tool call, `text` is a short label) |
    ask (needs a decision/answer from the user) | done (turn finished, `text` is the final result) |
    error. Local and remote workers emit the same events."""

    kind: str
    text: str = ""
    data: dict = field(default_factory=dict)


@runtime_checkable
class Worker(Protocol):
    """Background agent (Claude Code, local or remote)."""

    on_event: "Callable[[WorkerEvent], None] | None"

    def send(self, text: str) -> None:
        """Start a new turn, interrupting any running one."""
        ...

    def interrupt(self) -> None: ...
    def answer(self, decision: str, text: str = "") -> bool:
        """Resolve a pending `ask`. decision: allow | always | deny | text. False if nothing pending."""
        ...
    def has_pending(self) -> bool: ...
    def close(self) -> None: ...


@runtime_checkable
class AudioOut(Protocol):
    def play(self, audio: Audio) -> None: ...
    def cancel(self) -> None: ...
    def wait(self) -> None: ...
