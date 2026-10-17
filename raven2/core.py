"""Headless core loop: text in -> commands | brain -> TTS -> audio out, with barge-in.
Depends only on the ports in interfaces.py."""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from typing import Callable

from . import approvals, commands, packs, personas as P
from .brain import Brain
from .interfaces import STT, TTS, Audio, AssetMissing, AudioOut, Worker, WorkerEvent


@dataclass
class Services:
    stt: STT
    tts: TTS
    out: AudioOut
    worker: Worker
    brain: Brain


class StartupError(RuntimeError):
    pass


class Assistant:
    def __init__(self, svc: Services, personas: dict[str, P.Persona], log: Callable[[str], None] = print,
                 ack_mode: str = "generated"):
        self.svc, self.personas, self.log, self.ack_mode = svc, personas, log, ack_mode
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._speech_lock = threading.Lock()  # one voice at a time; worker readouts queue behind each other
        self._speak_error = ""
        self._tools = 0
        self._ask_kind = ""
        svc.worker.on_event = self._on_worker_event

    @property
    def persona(self) -> P.Persona:
        return self.svc.brain.persona

    # ── input ──
    def barge_in(self) -> None:
        """User started talking/typing: silence everything immediately."""
        self._cancel.set()
        self.svc.out.cancel()

    def handle_audio(self, samples, sample_rate: int = 16_000) -> None:
        text = self.svc.stt.transcribe(samples, sample_rate)
        if text:
            self.handle_text(text)

    def handle_text(self, text: str) -> None:
        self.log(f"you: {text}")
        self.barge_in()
        cmd = commands.match(text, self.personas)
        if cmd:
            return self._run_command(cmd)
        w = self.svc.worker
        if w.has_pending():
            return self._answer_pending(text)
        w.send(text)  # always forwarded (interrupting any running turn); the worker decides what to do
        if getattr(w, "background", True):
            self._tools = 0
            self._start(lambda: self._ack(text))
        else:
            self._start(lambda: self.svc.brain.reply(text))

    def _ack(self, text: str):
        """Generated acknowledgement; falls back to a canned persona line if the model fails."""
        spoke = False
        try:
            for c in (self.svc.brain.acknowledge(text) if self.ack_mode == "generated" else ()):
                spoke = True
                yield c
        except Exception as e:
            self.log(f"[ack fallback] {type(e).__name__}: {e}")
        if not spoke:
            yield random.choice(self.persona.acks or ["On it."])

    # ── worker events ──
    def _answer_pending(self, text: str) -> None:
        w = self.svc.worker
        if self._ask_kind == "question":
            w.answer("text", text)
            return
        d = approvals.parse_decision(text)
        if d is None:
            return self.say("Sorry, was that a yes or a no?")
        w.answer(d, "" if d != "deny" else "The user said no.")

    def _on_worker_event(self, ev: WorkerEvent) -> None:
        if ev.kind == "tool":
            self._tools += 1
        elif ev.kind == "ask":
            self._ask_kind = ev.data.get("ask_type", "permission")
            if self._ask_kind == "question":
                self.say(ev.text)
            else:
                self.say(f"{ev.text.rstrip('.?')}. Yes, always, or no?")
        elif ev.kind == "done":
            tools, result = self._tools, ev.text or "Finished, with no summary."
            self._start(lambda: self.svc.brain.relay(result, tools), cancel_current=False)
        elif ev.kind == "error":
            self.log(f"[worker error] {ev.text}")
            self.say("Something went wrong on the Claude side. Check the log.", cancel_current=False)

    # ── commands ──
    def _run_command(self, cmd: commands.Command) -> None:
        if cmd.kind == "stop":
            return
        if cmd.kind == "list":
            return self.say(commands.describe_list(self.personas, self.persona.id))
        if cmd.kind == "switch" and cmd.persona:
            self.switch(cmd.persona)
            self.say(cmd.persona.greeting or f"{cmd.persona.name} here.")

    def prepare(self, persona: P.Persona | None = None) -> None:
        """Download/load the voice now, so progress shows before the first reply."""
        persona = persona or self.persona
        prep = getattr(self.svc.tts, "prepare", None)
        if not prep:
            return
        try:
            prep(persona.voice)
        except AssetMissing:
            packs.install_pack(persona.id, persona.name)  # download + install, then load
            prep(persona.voice)

    def startup(self, intro: bool = True) -> None:
        """Load the STT and LLM models up front (blocking), then have the model introduce itself.
        The spoken intro doubles as the 'I'm up' signal. There is deliberately no fallback: if a model
        fails to load this raises StartupError, and the caller shows it and exits."""
        self.log("loading models...")
        self._warm()
        if intro:
            self._start(self.svc.brain.intro)
            self.wait()
            if self._speak_error:
                raise StartupError(self._speak_error)

    def _warm(self) -> None:
        import numpy as np

        for label, fn in (("speech-to-text", lambda: self.svc.stt.transcribe(np.zeros(16_000, np.float32))),
                          ("language model", self.svc.brain.warm)):
            try:
                fn()
            except Exception as e:
                raise StartupError(f"{label} failed to load: {type(e).__name__}: {e}") from e

    def switch(self, persona: P.Persona) -> None:
        self.prepare(persona)
        self.svc.brain.set_persona(persona)
        P.set_active_id(persona.id)
        self.log(f"persona -> {persona.name}")

    # ── output ──
    def say(self, text: str, cancel_current: bool = True) -> None:
        self._start(lambda: iter([text]), cancel_current)

    def _start(self, chunks: Callable[[], object], cancel_current: bool = True) -> None:
        with self._lock:
            if cancel_current:
                self._cancel.set()
            reuse = not cancel_current and not self._cancel.is_set()
            self._cancel = cancel = self._cancel if reuse else threading.Event()
            self._thread = threading.Thread(target=self._speak, args=(chunks, cancel), daemon=True)
            self._thread.start()

    def _speak(self, chunks, cancel: threading.Event) -> None:
        with self._speech_lock:
            self._speak_locked(chunks, cancel)

    def _speak_locked(self, chunks, cancel: threading.Event) -> None:
        persona = self.persona  # fixed for this utterance even if a switch lands mid-stream
        gen = chunks()
        try:
            for chunk in gen:
                if cancel.is_set():
                    break
                self.log(f"{persona.name}: {chunk}")
                for audio in self.svc.tts.synthesize(chunk, persona.voice):
                    if cancel.is_set():
                        break
                    self.svc.out.play(audio)
        except Exception as e:
            self._speak_error = f"{type(e).__name__}: {e}"
            self.log(f"[error] {self._speak_error}")
        finally:
            close = getattr(gen, "close", None)
            if close:
                close()

    def wait(self) -> None:
        t = self._thread
        if t:
            t.join()
        self.svc.out.wait()
