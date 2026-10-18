"""Small numpy effects so one TTS model can sound like several characters.
FxChain is stateful across chunks (phase, echo tail) so sentence boundaries don't click or pump."""

from __future__ import annotations

import numpy as np


def resample(x: np.ndarray, ratio: float) -> np.ndarray:
    """ratio > 1 shortens (raises pitch when played at the original rate)."""
    n = max(1, int(len(x) / ratio))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def soft_limit(y: np.ndarray, knee: float = 0.8) -> np.ndarray:
    """Transparent below `knee`; above it, peaks are rounded off smoothly toward +-1 instead of clipping.
    (A plain tanh bends every sample, which colors the whole voice and distorts it even at normal levels.)"""
    a = np.abs(y)
    over = a > knee
    out = y.astype(np.float32).copy()
    out[over] = np.sign(y[over]) * (knee + (1 - knee) * np.tanh((a[over] - knee) / (1 - knee)))
    return out


class FxChain:
    """Keys: ring_hz + ring_mix (robotic), comb_ms + comb_mix (metallic echo), crush_bits, gain.
    Output is soft-limited, never re-normalized per chunk, so loudness stays consistent."""

    def __init__(self, sr: int, fx: dict):
        self.sr, self.fx = sr, fx or {}
        self.pos = 0
        self.delay = max(1, int(sr * float(self.fx.get("comb_ms", 0)) / 1000)) if self.fx.get("comb_ms") else 0
        self.tail = np.zeros(self.delay, np.float32)

    def process(self, x: np.ndarray) -> np.ndarray:
        fx = self.fx
        if not fx or len(x) == 0:
            return x
        y = x.astype(np.float32)
        if fx.get("ring_hz"):
            mix = float(fx.get("ring_mix", 0.4))
            t = (np.arange(len(y)) + self.pos) / self.sr
            y = (1 - mix) * y + mix * y * np.sin(2 * np.pi * float(fx["ring_hz"]) * t).astype(np.float32)
        if self.delay:
            buf = np.concatenate([self.tail, y])
            y = y + float(fx.get("comb_mix", 0.35)) * buf[: len(y)]
            self.tail = buf[len(y): len(y) + self.delay] if len(buf) >= len(y) + self.delay else \
                np.concatenate([np.zeros(self.delay - (len(buf) - len(y)), np.float32), buf[len(y):]])
        if fx.get("crush_bits"):
            levels = 2 ** int(fx["crush_bits"])
            y = np.round(y * levels) / levels
        self.pos += len(x)
        return soft_limit(y * float(fx.get("gain", 1.0)))
