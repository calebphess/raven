"""Composition root: the only place that turns a profile into concrete backends."""

from __future__ import annotations

from . import config, personas as P, registry
from .brain import Brain
from .core import Assistant, Services


def build(profile_name: str | None = None, persona_id: str | None = None, log=print,
          worker: str | None = None, cwd: str | None = None) -> Assistant:
    profile = config.load_profile(profile_name)
    if worker:
        profile["worker"] = {**profile["worker"], "backend": worker}
    if cwd:
        profile["worker"]["cwd"] = cwd

    def make(kind: str):
        spec = dict(profile[kind])
        return registry.create(kind, spec.pop("backend"), **spec)

    allp = P.load_all()
    persona = allp.get(persona_id or P.get_active_id()) or allp["dottie"]
    user, _ = config.user_identity()
    svc = Services(stt=make("stt"), tts=make("tts"), out=make("audio_out"),
                   worker=make("worker"), brain=Brain(make("llm"), persona, user))
    return Assistant(svc, allp, log)
