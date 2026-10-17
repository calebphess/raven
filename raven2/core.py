"""Headless core loop: text in -> commands | brain -> TTS -> audio out, with barge-in.
Depends only on the ports in interfaces.py."""

from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import approvals, commands, config, packs, personas as P
from .brain import Brain, verbatim_chunks
from .interfaces import STT, TTS, Audio, AssetMissing, AudioOut, Worker, WorkerEvent


@dataclass
class Services:
    stt: STT
    tts: TTS
    out: AudioOut
    worker: Worker
    brain: Brain


def _short_label(label: str) -> str:
    """'Read: /a/b/c.py' -> 'Read: c.py' so the progress model isn't tempted to read out paths."""
    return re.sub(r"(?:/[\w.\-]+)+/([\w.\-]+)", r"\1", label)[:70]


class StartupError(RuntimeError):
    pass


class Assistant:
    def __init__(self, svc: Services, personas: dict[str, P.Persona], log: Callable[[str], None] = print,
                 ack_mode: str = "generated", status=None, progress: bool = True):
        self.svc, self.personas, self.log, self.ack_mode = svc, personas, log, ack_mode
        self.status = status  # optional raven2.status.Status; None = no visual indicator
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._speech_lock = threading.Lock()  # one voice at a time; worker speech queues behind each other
        self._speak_error = ""
        self._tools = 0
        self._persona_changed = False
        self._ask_kind = ""
        # Worker-turn state. Raven only speaks an ack and short progress lines itself; Claude's own
        # <raven> blocks are spoken verbatim and are the real answer.
        self._working = False
        self._spoke_verbatim = False
        self._activity: list[str] = []
        self._say_seq = 0  # bumps whenever Claude speaks, so late progress lines can tell they're stale
        self._last_voice = 0.0
        self._progress_count = 0
        self._progress_thread: threading.Thread | None = None
        self.progress_enabled, self.progress_first, self.progress_gap = progress, 6.0, 12.0
        svc.worker.on_event = self._on_worker_event
        svc.worker.controls = self  # lets the worker list/switch personas on this live session

    @property
    def persona(self) -> P.Persona:
        return self.svc.brain.persona

    # ── input ──
    def _st(self, name: str, *args) -> None:
        if self.status is not None:
            getattr(self.status, name)(*args)

    def barge_in(self) -> None:
        """User started talking/typing: silence everything immediately."""
        self._cancel.set()
        self.svc.out.cancel()

    def handle_audio(self, samples, sample_rate: int = 16_000) -> None:
        self._st("listen", "transcribing")
        try:
            text = self.svc.stt.transcribe(samples, sample_rate)
        finally:
            self._st("listen", "")
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
            self._st("working", True)
            self._tools, self._persona_changed = 0, False
            self._begin_turn()
            self._start(lambda: self._ack(text))
        else:
            self._start(lambda: self.svc.brain.reply(text))

    def _ack(self, text: str):
        """Generated acknowledgement; falls back to a canned persona line if the model fails."""
        spoke = False
        # "next", "yes please", "go on": there's nothing to guess a first step for, so don't invent one.
        generate = self.ack_mode == "generated" and len(text.split()) > 3
        try:
            for c in (self.svc.brain.acknowledge(text) if generate else ()):
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
            self._st("working")
            return
        d = approvals.parse_decision(text)
        if d is None:
            return self.say("Sorry, was that a yes or a no?")
        w.answer(d, "" if d != "deny" else "The user said no.")
        self._st("working")

    def _on_worker_event(self, ev: WorkerEvent) -> None:
        if ev.kind == "say":  # Claude's own words for the user: spoken exactly as written
            self._spoke_verbatim = True
            self._say_seq += 1
            self._last_voice = time.monotonic()
            self._start(lambda: verbatim_chunks(ev.text), cancel_current=False)
        elif ev.kind == "tool":
            if not ev.text.startswith(("mcp__raven__", "ToolSearch")):  # session plumbing, not user work
                self._tools += 1
                self._activity.append(_short_label(ev.text))
                self._st("tool")
        elif ev.kind == "ask":
            self._ask_kind = ev.data.get("ask_type", "permission")
            self._last_voice = time.monotonic()
            self._st("waiting", "yes, always, or no?" if self._ask_kind == "permission" else "question")
            if self._ask_kind == "question":
                self.say(ev.text)
            else:
                self.say(f"{ev.text.rstrip('.?')}. Yes, always, or no?")
        elif ev.kind == "done":
            self._working = False
            self._st("done")
            spoke, changed = self._spoke_verbatim, self._persona_changed
            self._spoke_verbatim = self._persona_changed = False
            if spoke or (changed and self._tools == 0):  # Claude already spoke / a switch spoke for itself
                return
            # Fallback only: Claude finished without a <raven> block, so summarize what it wrote.
            tools, result = self._tools, ev.text or "Finished, with no summary."
            self._start(lambda: self.svc.brain.relay(result, tools), cancel_current=False)
        elif ev.kind == "error":
            self._working = False
            self._st("error", ev.text[:60])
            self.log(f"[worker error] {ev.text}")
            self.say("Something went wrong on the Claude side. Check the log.", cancel_current=False)

    # ── progress: short awareness lines while the worker is busy ──
    def _begin_turn(self) -> None:
        self._working, self._spoke_verbatim = True, False
        self._activity, self._progress_count = [], 0
        self._last_voice = time.monotonic()
        if self.progress_enabled and self._progress_thread is None:
            self._progress_thread = threading.Thread(target=self._progress_loop, daemon=True, name="progress")
            self._progress_thread.start()

    def _progress_loop(self) -> None:
        while True:
            time.sleep(1.0)
            try:
                self._progress_tick(time.monotonic())
            except Exception as e:
                self.log(f"[progress] {type(e).__name__}: {e}")

    def _progress_tick(self, now: float) -> None:
        """Speak one short update if Claude has been busy and quiet for a while. Never talks over speech,
        never while waiting on the user, and says nothing if there is no new activity."""
        if not (self.progress_enabled and self._working) or not self._activity:
            return
        if self.svc.worker.has_pending() or self._speech_lock.locked():
            return
        gap = self.progress_first if self._progress_count == 0 else self.progress_gap
        if now - self._last_voice < gap:
            return
        acts, self._activity = self._activity, []
        self._last_voice, self._progress_count = now, self._progress_count + 1
        self._start(lambda: self._progress_line(acts), cancel_current=False)

    def _progress_line(self, acts: list[str]):
        """Generate the line, then drop it if Claude has since spoken or finished: stale progress is noise."""
        said = self._say_seq
        lines = list(self.svc.brain.progress(acts))
        if self._working and self._say_seq == said:
            yield from lines

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

    # ── RavenControls (called by the worker from its own thread) ──
    def list_personas(self) -> list[dict]:
        self.personas = P.load_all()  # include packs installed since startup
        return [{"id": p.id, "name": p.name, "aliases": p.aliases, "active": p.id == self.persona.id,
                 "default": p.id == P.DEFAULT_ID} for p in self.personas.values()]

    def set_persona(self, query: str) -> str:
        self.personas = P.load_all()
        persona = P.find(query, self.personas)
        if persona is None:
            raise ValueError(f"No persona matches {query!r}. Available: "
                             + ", ".join(p.name for p in self.personas.values()))
        try:
            self.switch(persona)  # loads the voice (downloading its pack if needed), then swaps brain + voice
        except (AssetMissing, packs.PackError) as e:
            raise ValueError(f"Couldn't load {persona.name}: {e}") from e
        self._persona_changed = True
        self._start(self.svc.brain.intro)  # the new persona announces itself in its own voice
        return f"Switched to {persona.name}."

    def persona_brief(self) -> str:
        user, _ = config.user_identity()
        p = self.persona
        return (f"Active persona: {p.name}. Write your <raven> text in this persona's voice (tone only; keep the "
                f"facts exact): " + p.personality.replace("{user}", user or "the user"))

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
