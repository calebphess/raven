from __future__ import annotations

from pathlib import Path
from typing import Iterator

import numpy as np

from .. import audio_fx
from ..config import MODELS_DIR, PERSONAS_DIR
from ..interfaces import Audio, AssetMissing, VoiceSpec

def ensure_voice(voice_id: str) -> Path:
    """Find an installed voice (shared models dir or any installed persona pack)."""
    for root in (PERSONAS_DIR, MODELS_DIR):
        hits = sorted(root.rglob(f"{voice_id}.onnx")) if root.is_dir() else []
        if hits:
            return hits[0]
    raise AssetMissing(voice_id)


class PiperTTS:
    def __init__(self, **_):
        self._voices: dict[str, object] = {}

    def prepare(self, voice: VoiceSpec) -> None:
        """Fetch/load the voice now (shows download progress) instead of on first utterance."""
        self._voice(voice.model)

    def _voice(self, voice_id: str):
        if voice_id not in self._voices:
            from piper import PiperVoice

            self._voices[voice_id] = PiperVoice.load(str(ensure_voice(voice_id)))
        return self._voices[voice_id]

    def synthesize(self, text: str, voice: VoiceSpec) -> Iterator[Audio]:
        from piper import SynthesisConfig

        v = self._voice(voice.model)
        fx = dict(voice.effects)
        pitch = float(fx.pop("pitch", 0.0))  # semitones; compensate speed so it only moves pitch
        ratio = 2 ** (pitch / 12)
        p = voice.params
        cfg = SynthesisConfig(
            speaker_id=p.get("speaker_id"),
            length_scale=float(p.get("length_scale", 1.0)) * ratio,
            noise_scale=p.get("noise_scale"),
            noise_w_scale=p.get("noise_w_scale"),
            normalize_audio=False,  # per-sentence normalization makes loudness jump between chunks
            volume=float(p.get("volume", 3.0)),
        )
        sr = v.config.sample_rate
        chain = audio_fx.FxChain(sr, fx)
        for chunk in v.synthesize(text, cfg):
            x = chunk.audio_int16_array.astype(np.float32) / 32768.0
            if ratio != 1.0:
                x = audio_fx.resample(x, ratio)
            yield Audio(chain.process(x), sr)
