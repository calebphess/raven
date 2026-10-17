"""Headless core loop: text in -> commands | brain -> TTS -> audio out, with barge-in.
Depends only on the ports in interfaces.py."""

from __future__ import annotations

import os
import queue
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import approvals, commands, config, packs, personas as P, terminal
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
        # Speech is one FIFO queue drained by one thread, so utterances play strictly in the order they were
        # queued: Raven's instant ack always comes first and Claude's words wait behind it. `_epoch` is the
        # cancel mechanism: bumping it drops everything queued and stops the utterance in progress.
        self._q: queue.Queue = queue.Queue()
        self._epoch = 0
        self._pending = 0
        self._listening = False
        self._cond = threading.Condition()
        self.ack_deadline = 2.0  # seconds the model gets to produce an ack before the canned line is used
        threading.Thread(target=self._speaker_loop, daemon=True, name="speaker").start()
        self._speak_error = ""
        self._tools = 0
        self._persona_changed = False
        self._ask_kind = ""
        self._manual_terminal = None
        self.project_dir: str | None = None
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
        if name == "listen":  # the user is speaking (or being transcribed): Raven must not talk over them
            self._listening = bool(args and args[0])
        if self.status is not None:
            getattr(self.status, name)(*args)

    def barge_in(self) -> None:
        """User started talking/typing: silence everything immediately, queued speech included."""
        with self._cond:
            self._epoch += 1
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
        if cmd and cmd.kind == "chat":
            # Small talk aimed at Raven ("how are you", "good job"): answer locally, right now. It is not
            # forwarded, so it neither bothers Claude nor interrupts a task in progress. If a permission is
            # outstanding, fall through so "yes, thanks" is still read as an answer.
            if not self.svc.worker.has_pending():
                return self._start(lambda: self.svc.brain.chat(text))
        elif cmd:
            return self._run_command(cmd)
        w = self.svc.worker
        if getattr(w, "detached", False):  # a terminal owns the session; never write to it from here
            return self.say("Claude is open in the terminal. Tell me to hand it back when you're done.")
        if w.has_pending():
            return self._answer_pending(text)
        if getattr(w, "background", True):
            self._st("working", True)
            self._tools, self._persona_changed = 0, False
            self._begin_turn()
            self._start(lambda: self._ack(text))  # queued BEFORE the send, so nothing from Claude can precede it
            w.send(text)  # always forwarded (interrupting any running turn); the worker decides what to do
        else:
            w.send(text)
            self._start(lambda: self.svc.brain.reply(text))

    def _ack(self, text: str):
        """The first thing spoken for a request. It must be quick: the model gets `ack_deadline` seconds, and
        if it is slow, fails, or says something unusable, the persona's canned line is spoken instead."""
        canned = random.choice(self.persona.acks or ["On it."])
        # "next", "yes please", "go on": there's nothing to guess a first step for, so don't invent one.
        if self.ack_mode != "generated" or len(text.split()) <= 3:
            yield canned
            return
        box: queue.Queue = queue.Queue()

        def work():
            try:
                box.put(list(self.svc.brain.acknowledge(text)))
            except Exception as e:
                box.put(e)

        threading.Thread(target=work, daemon=True, name="ack").start()
        try:
            out = box.get(timeout=self.ack_deadline)
        except queue.Empty:
            self.log(f"[ack fallback] model took over {self.ack_deadline:.0f}s")
            yield canned
            return
        if isinstance(out, Exception):
            self.log(f"[ack fallback] {type(out).__name__}: {out}")
        yield from (out if (out and not isinstance(out, Exception)) else [canned])

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
                self.say(ev.text, cancel_current=False)
            else:  # queued behind whatever is being said (e.g. the ack), never cutting it off
                self.say(f"{ev.text.rstrip('.?')}. Yes, always, or no?", cancel_current=False)
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
        if self.svc.worker.has_pending() or self._pending > 0:
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
        if cmd.kind == "open_terminal":
            return self._say_after(self.open_manual_session, "Opening Claude in the terminal.")
        if cmd.kind == "handback":
            return self._say_after(self.hand_back, "Back in control.")
        if cmd.kind == "switch" and cmd.persona:
            self.switch(cmd.persona)
            self.say(cmd.persona.greeting or f"{cmd.persona.name} here.")

    def _say_after(self, action: Callable[[], object], ok: str) -> None:
        try:
            action()
        except Exception as e:
            self.log(f"[error] {e}")
            return self.say(f"I couldn't do that. {e}")
        self.say(ok)

    # ── manual takeover: the user drives the Claude session by hand in a terminal ──
    @property
    def manual(self) -> bool:
        return bool(getattr(self.svc.worker, "detached", False))

    def open_manual_session(self) -> dict:
        """Pause the background worker, release the session to a real terminal, and open it there.
        Raven forwards nothing while the terminal owns the session; hand_back() resumes it."""
        w = self.svc.worker
        info = w.session_info()
        if not info:
            raise RuntimeError("There's no Claude session yet. Send a request first.")
        cmd = terminal.claude_command(info["cwd"], info["session_id"], info.get("claude_path"))  # fail before pausing anything
        self.barge_in()
        w.detach()
        self._working, self._activity = False, []
        self._st("manual")
        try:
            self._manual_terminal = terminal.open_in_terminal(cmd, config.load_file().get("terminal", "Terminal"))
        except Exception:
            w.attach()  # the terminal never opened; don't leave Raven paused
            self._st("idle")
            raise
        return info

    def open_project(self, path: str) -> str:
        """Switch Claude to another project folder without restarting Raven (models stay loaded): a fresh
        session in that folder, and a clean slate for the local model's conversation."""
        if self.manual:
            raise RuntimeError("Hand Claude back to Raven first, then switch projects.")
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            raise FileNotFoundError(f"That folder doesn't exist: {path}")
        self.barge_in()
        self.svc.worker.open_project(path)
        self._working, self._activity, self._spoke_verbatim, self._persona_changed = False, [], False, False
        self.svc.brain.history.clear()
        self._st("idle")
        self.project_dir = path
        return path

    def hand_back(self) -> None:
        """Take the session back. The hand-driven Claude process is stopped and its window closed FIRST:
        resuming while it still runs would put two writers on one session. If it can't be stopped, this
        raises and Raven stays paused."""
        w = self.svc.worker
        info = w.session_info()
        if info:
            terminal.stop_session(info["session_id"])
        terminal.close_window(self._manual_terminal)
        self._manual_terminal = None
        w.attach()
        self._st("idle")

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

    def _rewarm(self) -> None:
        try:
            self.svc.brain.warm()
        except Exception as e:
            self.log(f"[rewarm] {type(e).__name__}: {e}")

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
        self._start(self.svc.brain.intro, cancel_current=False)  # the new persona announces itself, after the ack
        return f"Switched to {persona.name}."

    def persona_brief(self) -> str:
        user, _ = config.user_identity()
        p = self.persona
        return (f"Active persona: {p.name}. Write your <raven> text in this persona's voice (tone only; keep the "
                f"facts exact): " + p.personality.replace("{user}", user or "the user"))

    def switch(self, persona: P.Persona) -> None:
        self.prepare(persona)
        self.svc.brain.set_persona(persona)
        # A new persona means new prompts; re-read them now so the first reply isn't a cold, slow one.
        threading.Thread(target=self._rewarm, daemon=True, name="rewarm").start()
        P.set_active_id(persona.id)
        self.log(f"persona -> {persona.name}")

    # ── output ──
    def say(self, text: str, cancel_current: bool = True) -> None:
        self._start(lambda: iter([text]), cancel_current)

    def _start(self, chunks: Callable[[], object], cancel_current: bool = True) -> None:
        """Queue an utterance. cancel_current=True replaces whatever is queued or playing; False waits its turn."""
        with self._cond:
            if cancel_current:
                self._epoch += 1
            self._pending += 1
            epoch = self._epoch
        if cancel_current:
            self.svc.out.cancel()
        self._q.put((epoch, chunks))

    def _speaker_loop(self) -> None:
        while True:
            epoch, chunks = self._q.get()
            hold = time.monotonic() + 15  # safety cap so a stuck overlay can never mute Raven forever
            while self._listening and epoch == self._epoch and time.monotonic() < hold:
                time.sleep(0.05)  # when the user's message lands, barge_in drops this; a stray tap lets it play
            try:
                if epoch == self._epoch:  # skip anything that was cancelled while it waited
                    self._speak(chunks, epoch)
            except Exception as e:  # nothing may ever kill this thread: a dead speaker mutes Raven for good
                self._speak_error = f"{type(e).__name__}: {e}"
                self.log(f"[error] speech job failed: {self._speak_error}")
            finally:
                with self._cond:
                    self._pending -= 1
                    self._cond.notify_all()

    def _speak(self, chunks, epoch: int) -> None:
        persona = self.persona  # fixed for this utterance even if a switch lands mid-stream
        gen = None
        try:
            gen = chunks()
            for chunk in gen:
                if epoch != self._epoch:
                    break
                self.log(f"{persona.name}: {chunk}")
                for audio in self.svc.tts.synthesize(chunk, persona.voice):
                    if epoch != self._epoch:
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
        """Block until everything queued to be spoken has been spoken (or dropped)."""
        with self._cond:
            self._cond.wait_for(lambda: self._pending == 0)
        self.svc.out.wait()
