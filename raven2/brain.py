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


def system_prompt(persona: Persona, user_name: str = "") -> str:
    who = f"The user's name is {user_name}." if user_name else ""
    return f"{BASE_RULES} {who}\n\n" + persona.personality.replace("{user}", user_name or "the user")


def limit_sentences(pieces: Iterator[str], n: int) -> Iterator[str]:
    """Stop the token stream right after the n-th sentence end (truncating mid-piece), so the limit holds
    no matter how the speech chunker groups text. A '.' between digits ("3.5") is not a sentence end,
    including when the '.' and the digits arrive as separate pieces."""
    seen, last, pending_dot = 0, "", False
    for piece in pieces:
        if pending_dot:  # a '.' ended the previous piece right after a digit; decide now
            pending_dot = False
            if not piece[:1].isdigit():
                seen += 1
                if seen == n:
                    return  # that '.' was already yielded; stop before this piece
        for i, ch in enumerate(piece):
            if ch not in ".!?":
                continue
            nxt = piece[i + 1] if i + 1 < len(piece) else ""
            prev = piece[i - 1] if i else last
            if ch == "." and nxt.isdigit():
                continue  # decimal point
            if ch == "." and not nxt and prev.isdigit():
                pending_dot = True  # maybe a decimal; the next piece tells
                continue
            seen += 1
            if seen == n:
                yield piece[: i + 1]
                return
        last = piece[-1:] or last
        yield piece


# Sentence ends followed by whitespace, so "$186.03" and "1,000" are never cut mid-number. Commas are NOT
# boundaries: each chunk is synthesized on its own (with its own start/stop pause), so cutting at commas made
# one sentence sound like several clipped ones.
_SENTENCE = re.compile(r"[.!?…](?=\s)|\n")
_SOFT = re.compile(r"[,;:](?=\s)")  # only a fallback for a very long run with no sentence end


def clauses(pieces: Iterator[str], first_min: int = 12, min_len: int = 30, max_len: int = 160) -> Iterator[str]:
    """Group streamed tokens into speakable chunks at sentence boundaries. The first chunk may be short
    (latency); later ones wait for a sentence end of at least min_len characters so prosody stays natural.
    Only if max_len characters pass with no sentence end is the text cut at the last comma."""
    buf, n = "", 0
    for piece in pieces:
        buf += piece
        while True:
            limit = first_min if n == 0 else min_len
            cut = next((m.end() for m in _SENTENCE.finditer(buf) if len(buf[: m.end()].strip()) >= limit), None)
            if cut is None and len(buf) > max_len:
                cut = next((m.end() for m in reversed(list(_SOFT.finditer(buf, 0, max_len))) if m.end() >= limit), None)
            if cut is None:
                break
            yield buf[:cut].strip()
            buf, n = buf[cut:], n + 1
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

    # Words that assert an outcome. The ack may guess the first step ("Scanning the project"), never a result.
    _CLAIM = re.compile(
        r"\d|\b(there (is|are)|exists?|contains?|uploaded|pushed|committed|created|deleted|found|"
        r"done|finished|complete[d]?|fixed|passed|failed|unchanged|updated)\b", re.I)
    # An ack must START with the action ("Scanning...", "Let me...", "On it"), optionally after a short
    # in-character lead-in ("Very well, sir. Checking..."), and must not be a statement ("X is ...").
    _ACTION = re.compile(r"^(?:[\w' ]{1,25}[,.!]\s*){0,2}(?:[a-z]+ing\b|let me\b|i'?ll\b|i will\b|on it\b)", re.I)
    _STATEMENT = re.compile(r"\b(is|are|was|were|it's|this|that's)\b", re.I)

    # Restating the format right after the request keeps a 1B model from answering it.
    _ACK_SUFFIX = "\n(Reply only with the first step you are taking now, e.g. \"Scanning the files.\")"

    _ACK_SHOTS = [
        ("Explain what this project is about.", "Scanning the project files."),
        ("Why is the build failing?", "Checking the build output."),
        ("Commit this to git.", "Looking at the git status."),
        ("Add a dark mode to the settings page.", "Finding the settings code."),
        ("What's the weather in Denver?", "Looking that up."),
    ]

    def _ack_messages(self, text: str = "Explain what this project is about.") -> list[dict]:
        """Prompt for the instant acknowledgement: guess the *first step* of the task, in character.
        The guess may be wrong; the request itself goes to the worker untouched."""
        system = (self.persona.personality.replace("{user}", self.user_name or "the user")
                  + " The user has handed a task to your engineer, who is starting now. Say the likely FIRST "
                  "STEP as an action in progress, at most 6 words, in character. Never state a result, "
                  "an answer, or a number.")
        messages = [{"role": "system", "content": system}]
        for q, a in self._ACK_SHOTS:
            messages += [{"role": "user", "content": q + self._ACK_SUFFIX}, {"role": "assistant", "content": a}]
        messages.append({"role": "user", "content": text + self._ACK_SUFFIX})
        return messages

    def acknowledge(self, text: str) -> Iterator[str]:
        """Instant, in-voice acknowledgement that assumes the first step ("Scanning the project").
        A reply that states a result or doesn't sound like work starting is dropped (caller falls back
        to a canned persona line); a *wrong guess* is fine, since Claude acts on the raw request."""
        out = "".join(self.llm.stream(self._ack_messages(text), max_tokens=18, temperature=0.7)).replace('"', "").strip()
        if not out or len(out.split()) > 9 or self._CLAIM.search(out) or not self._ACTION.search(out) \
                or self._STATEMENT.search(out):
            return
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": out}]
        yield out

    def warm(self) -> None:
        """Load the model and prefill the persona prompt so the first real reply isn't a cold start."""
        messages = [{"role": "system", "content": system_prompt(self.persona, self.user_name)},
                    {"role": "user", "content": "Hi."}]
        for msgs in (messages, self._ack_messages()):  # chat prompt and ack prompt, both on the hot path
            for _ in self.llm.stream(msgs, max_tokens=1):
                pass

    def intro(self) -> Iterator[str]:
        """Fresh, in-voice startup line. Speaking it is also the signal that the model is up."""
        messages = [{"role": "system", "content": system_prompt(self.persona, self.user_name)}]
        if self.persona.greeting:  # one example line sets the voice; high temperature keeps it varied
            messages += [{"role": "user", "content": "You have just started up. Say you are up and ready."},
                         {"role": "assistant", "content": self.persona.greeting}]
        messages.append({"role": "user", "content": "You have just started up. In two very short sentences, say you are up and running, then either "
                         "say you are ready to begin or ask how you can help. Do not say your name. "
                         "Use different words than before."})
        stream = limit_sentences(self.llm.stream(messages, max_tokens=40, temperature=1.0), 2)
        for c in clauses(stream, first_min=10):
            yield c.replace('"', "")

    _PROGRESS_SHOTS = [
        (["Read: README.md", "Grep: login"], "Reading the readme and searching for login code."),
        (["Bash: pytest -q"], "Running the tests."),
        (["Edit: settings.py", "Edit: theme.css"], "Editing the settings and theme files."),
        (["WebSearch: stock price"], "Searching the web."),
    ]

    def progress(self, activity: list[str]) -> Iterator[str]:
        """One short awareness line about what the worker is doing right now. Optional by nature: if the
        model's line isn't a plain present-progressive action, say nothing rather than guess."""
        system = (self.persona.personality.replace("{user}", self.user_name or "the user")
                  + " Your engineer is busy working. Give the user a quick awareness update: ONE short sentence, "
                  "at most 10 words, present progressive, in character. Never state results or conclusions.")
        messages = [{"role": "system", "content": system}]
        for acts, line in self._PROGRESS_SHOTS:
            messages += [{"role": "user", "content": "\n".join(acts)}, {"role": "assistant", "content": line}]
        messages.append({"role": "user", "content": "\n".join(activity[-6:])})
        out = "".join(self.llm.stream(messages, max_tokens=24, temperature=0.6)).replace('"', "").strip()
        if (not out or len(out.split()) > 12 or self._CLAIM.search(out) or self._STATEMENT.search(out)
                or not self._ACTION.search(out)):
            return
        yield out


def verbatim_chunks(text: str) -> Iterator[str]:
    """Split text Claude wrote for speech into sentence-sized chunks so playback starts early.
    Nothing is rewritten: joined, the chunks are exactly the text."""
    yield from clauses(iter([text]), first_min=14, min_len=45)
