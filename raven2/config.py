"""Profiles and paths. ~/.raven/config.toml overrides built-in profiles; env overrides identity."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

HOME = Path(os.environ.get("RAVEN_HOME", Path.home() / ".raven"))
MODELS_DIR = HOME / "models"
PERSONAS_DIR = HOME / "personas"
STATE_FILE = HOME / "state.json"
CONFIG_FILE = HOME / "config.toml"

# Each slot names a registered backend plus its params. Swap by editing config.toml or a profile.
PROFILES: dict[str, dict] = {
    "intel-cpu": {
        "stt": {"backend": "faster-whisper", "model": "base", "compute_type": "int8"},
        "llm": {"backend": "llama-cpp", "model": "Llama-3.2-1B-Instruct-Q4_K_M.gguf", "n_ctx": 2048},
        "tts": {"backend": "piper", "speed": 1.3},  # speaking-rate multiplier (1.0 = natural)
        "audio_out": {"backend": "sounddevice"},
        "worker": {"backend": "null"},
        "ack": "generated",  # or "canned": persona lines, instant, no model call
    },
    "apple-silicon": {
        "stt": {"backend": "faster-whisper", "model": "small", "compute_type": "int8"},
        "llm": {"backend": "llama-cpp", "model": "Llama-3.2-3B-Instruct-Q4_K_M.gguf", "n_ctx": 4096, "n_gpu_layers": -1},
        "tts": {"backend": "piper", "speed": 1.3},  # speaking-rate multiplier (1.0 = natural)
        "audio_out": {"backend": "sounddevice"},
        "worker": {"backend": "null"},
        "ack": "generated",
    },
}
DEFAULT_PROFILE = "intel-cpu"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_file(path: Path = CONFIG_FILE) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def load_profile(name: str | None = None, path: Path = CONFIG_FILE) -> dict:
    """Resolve the active profile: built-in -> user [profiles.<name>] -> user top-level slot overrides."""
    user = load_file(path)
    name = name or os.environ.get("RAVEN_PROFILE") or user.get("profile", DEFAULT_PROFILE)
    profile = copy.deepcopy(PROFILES.get(name, {}))
    profile = _merge(profile, user.get("profiles", {}).get(name, {}))
    for slot in ("stt", "llm", "tts", "audio_out", "worker"):
        if slot in user:
            profile = _merge(profile, {slot: user[slot]})
    if not profile:
        raise KeyError(f"unknown profile {name!r}; built-in: {sorted(PROFILES)}")
    profile["name"] = name
    return profile


def read_state() -> dict:
    import json

    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def update_state(**changes) -> None:
    """Merge into ~/.raven/state.json (persona, project folder, ...)."""
    import json

    HOME.mkdir(parents=True, exist_ok=True)
    state = read_state()
    state.update(changes)
    STATE_FILE.write_text(json.dumps(state))


def user_identity() -> tuple[str, str]:
    return os.environ.get("RAVEN_USER_NAME", ""), os.environ.get("RAVEN_USER_PRONOUNS", "")


def resolve_model(name_or_path: str, models_dir: Path | None = None) -> Path:
    models_dir = models_dir or MODELS_DIR  # looked up per call, not frozen at import
    p = Path(name_or_path).expanduser()
    if p.is_absolute() and p.exists():
        return p
    hits = sorted(models_dir.rglob(name_or_path))
    if hits:
        return hits[0]
    raise FileNotFoundError(f"model {name_or_path!r} not found under {models_dir}")
