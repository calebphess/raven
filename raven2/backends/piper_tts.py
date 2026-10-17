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


# About a fifth of a Piper utterance does not speed up when length_scale shrinks (the model's own pauses and
# minimum phoneme lengths), so asking for 1.3x via length_scale alone gave only ~1.2x. Measured on the Dottie and
# Charles voices: requested 1.3 -> 1.16-1.21x, 1.6 -> 1.43-1.45x, 2.0 -> 1.58-1.64x, which fits
# actual = 1 / (FIXED + (1 - FIXED) / requested) with FIXED ~ 0.2.
FIXED_SHARE = 0.2


def length_divisor(speed: float) -> float:
    """How much to divide length_scale by so the audio really is `speed` times faster."""
    speed = max(0.5, min(float(speed), 2.0))
    return (1 - FIXED_SHARE) / (1 / speed - FIXED_SHARE)


# Voices differ a lot in raw level (measured peaks: Dottie 0.26, Raven 0.51, Charles 0.72) and Piper clips at full
# scale, so one fixed boost clipped the louder voices. Each voice is measured once, on this reference text, and given
# the gain that puts its peaks near TARGET_PEAK. The reference has sharper consonants than typical speech, so
# ordinary lines land a little below the target and the loudest ones don't clip.
CALIBRATION_TEXT = "The quick brown fox jumps over the lazy dog. Then it rests by the river."
TARGET_PEAK = 0.85


class PiperTTS:
    def __init__(self, speed: float = 1.3, **_):
        # Global speaking-rate multiplier on top of each persona's own pacing: 1.3 = the audio is 30% shorter.
        self.speed = max(0.5, min(float(speed), 2.0))
        self._voices: dict[str, object] = {}
        self._gains: dict[str, float] = {}

    def prepare(self, voice: VoiceSpec) -> None:
        """Fetch/load the voice now (shows download progress) instead of on first utterance."""
        self._voice(voice.model)

    def _gain(self, voice_id: str) -> float:
        """The volume that brings this voice to TARGET_PEAK, measured once per voice."""
        if voice_id not in self._gains:
            from piper import SynthesisConfig

            peak = 0.0
            for chunk in self._voice(voice_id).synthesize(CALIBRATION_TEXT, SynthesisConfig(normalize_audio=False, volume=1.0)):
                a = chunk.audio_int16_array
                peak = max(peak, float(np.abs(a.astype(np.float32)).max()) / 32768.0) if len(a) else peak
            self._gains[voice_id] = max(0.5, min(TARGET_PEAK / peak, 6.0)) if peak > 1e-4 else 3.0
        return self._gains[voice_id]

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
            # length_scale is a duration multiplier, so faster speech divides it (calibrated, see FIXED_SHARE).
            # `ratio` keeps a pitch shift
            # from also changing the speed (see above).
            length_scale=float(p.get("length_scale", 1.0)) * ratio / length_divisor(self.speed),
            noise_scale=p.get("noise_scale"),
            noise_w_scale=p.get("noise_w_scale"),
            normalize_audio=False,  # per-sentence normalization makes loudness jump between chunks
            volume=float(p["volume"]) if "volume" in p else self._gain(voice.model),  # explicit override wins
        )
        sr = v.config.sample_rate
        chain = audio_fx.FxChain(sr, fx)
        for chunk in v.synthesize(text, cfg):
            x = chunk.audio_int16_array.astype(np.float32) / 32768.0
            if ratio != 1.0:
                x = audio_fx.resample(x, ratio)
            yield Audio(audio_fx.soft_limit(chain.process(x)), sr)  # final safety: round off any stray peak, never clip
