"""Front brain: persona-aware conversation over an LLM port."""

from __future__ import annotations

import re
from typing import Iterator

from .interfaces import LLM
from .personas import Persona

BASE_RULES = (
    "You are a voice assistant. Your replies are spoken aloud, so: use one to three short "
    "sentences, plain words, no markdown, no lists, no emojis, no stage directions, and never "
    "read out file paths, code, or URLs. Put the answer first."
)
_CLAUSE_END = re.compile(r"[.!?;:,…]\s*$|\n")


def system_prompt(persona: Persona, user_name: str = "") -> str:
    who = f"The user's name is {user_name}." if user_name else ""
    return f"{BASE_RULES} {who}\n\n" + persona.personality.replace("{user}", user_name or "the user")


def clauses(pieces: Iterator[str], first_min: int = 12, min_len: int = 30) -> Iterator[str]:
    """Group streamed tokens into speakable chunks. The first chunk is cut early (latency),
    later ones wait for a sentence-ish boundary so prosody stays natural."""
    buf, n = "", 0
    for piece in pieces:
        buf += piece
        limit = first_min if n == 0 else min_len
        if len(buf.strip()) >= limit and _CLAUSE_END.search(buf):
            yield buf.strip()
            buf, n = "", n + 1
    if buf.strip():
        yield buf.strip()


class Brain:
    def __init__(self, llm: LLM, persona: Persona, user_name: str = "", max_history: int = 8):
        self.llm, self.user_name, self.max_history = llm, user_name, max_history
        self.history: list[dict] = []
        self.set_persona(persona)

    def set_persona(self, persona: Persona) -> None:
        self.persona = persona
        self.history.clear()  # a new character shouldn't inherit the old one's voice

    def reply(self, text: str) -> Iterator[str]:
        """Yield speakable chunks; the full reply is recorded in history when exhausted or closed."""
        self.history.append({"role": "user", "content": text})
        self.history = self.history[-self.max_history * 2:]
        messages = [{"role": "system", "content": system_prompt(self.persona, self.user_name)}, *self.history]
        spoken: list[str] = []
        try:
            for c in clauses(self.llm.stream(messages)):
                spoken.append(c)
                yield c
        finally:
            self.history.append({"role": "assistant", "content": " ".join(spoken)})

    def relay(self, result: str, tools_used: int = 0) -> Iterator[str]:
        """Super-brief spoken readout of what the worker reported, in the persona's voice."""
        prompt = (
            "Your engineer, Claude, just finished and reported the text below. In ONE short sentence "
            "(under 20 words) tell the user the outcome, in your own voice. Say what was done or found, "
            "not how. Do not mention tool calls, file paths, code or long numbers. Do not ask questions.\n\n"
            "Report:\n" + result[:1500]
        )
        messages = [{"role": "system", "content": system_prompt(self.persona, self.user_name)},
                    *self.history[-4:], {"role": "user", "content": prompt}]
        spoken: list[str] = []
        try:
            for c in clauses(self.llm.stream(messages, max_tokens=60)):
                spoken.append(c)
                yield c
        finally:
            if spoken:
                self.history.append({"role": "assistant", "content": " ".join(spoken)})
