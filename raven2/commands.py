"""Zero-latency voice commands, matched before the LLM ever sees the text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .personas import Persona, find


@dataclass
class Command:
    kind: str  # "list" | "switch" | "stop"
    persona: Persona | None = None


_LIST = re.compile(
    r"\b(list|show|which|what|available)\b.*\b(personas?|voices?|characters?)\b"
    r"|\bwho can i (talk|speak) to\b|\bwho else is there\b"
)
_SWITCH = [
    r"^(?:please\s+)?switch(?:\s+(?:persona|voice))?\s+to\s+(.+)$",
    r"^(?:please\s+)?change(?:\s+(?:persona|voice))?\s+to\s+(.+)$",
    r"^(?:please\s+)?(?:use|become|be)\s+(.+?)(?:\s+(?:persona|voice))?$",
    r"^(?:put\s+on|let me talk to|get me|i want to talk to)\s+(.+)$",
]
_OPEN = re.compile(r"\b(open|show|take me to|let me (see|use|drive))\b.*\b(claude|session|terminal)\b")
_HANDBACK = re.compile(r"\b(hand (it )?back|take (it|over) back|resume (raven|the assistant)|i'?m done in the terminal|"
                       r"you can have (it|claude) back)\b")
_STOP = re.compile(r"^(stop|be quiet|shut up|cancel|never ?mind)[.!]?$")


# Purely conversational talk aimed at Raven itself. These must match the WHOLE utterance (after dropping
# fillers and Raven's names), so "thanks, now run the tests" or "how's it going with the build" is not
# swallowed here and still goes to Claude.
_CHAT = [re.compile(p) for p in (
    r"(hi|hello|hey|yo|howdy|good (morning|afternoon|evening|night)|hi there|hello there|hey there)( again)?",
    r"(how are you|how are you (doing|feeling)( today| tonight| now)?|how('s| is) it going|how have you been|"
    r"how do you feel|what'?s up|what is up|you (doing )?(ok|okay|alright)|are you (ok|okay|alright|there|awake|up)|"
    r"how('s| is) everything|how('s| is) your (day|night|evening))",
    r"(thanks|thank you|thanks a lot|thank you so much|thanks so much|many thanks|i appreciate (it|that|you)|cheers)",
    r"((that was |that is |that's |this is |you did |you('re| are) )?"
    r"(so |really |very |pretty )?(great|awesome|amazing|excellent|fantastic|wonderful|perfect|the best|brilliant|"
    r"a (star|legend|lifesaver)|doing (great|well|good|amazing)))",
    r"((good|nice|great|excellent|awesome|well) (job|work)|well done|nicely done|nice one|bravo|good (girl|boy))",
    r"(i (love|like) (you|it|that|this)|love (you|it|that|this)|you rock|you'?re the best|you are the best)",
    r"(who are you|what'?s your name|what is your name|what are you)",
    r"(good (bot|assistant)|nice to (meet|see|hear) you|nice to have you)",
)]
_FILLERS = re.compile(r"\b(please|so|um|uh|well|oh|hey|hi|just|buddy|friend|pal|dear)\b")


def is_chitchat(text: str, personas: dict[str, Persona]) -> bool:
    """True for short, purely conversational utterances directed at Raven (greetings, how-are-you, thanks,
    compliments). Deliberately conservative: anything with a task in it returns False."""
    t = re.sub(r"[^a-z' ]", " ", text.lower())
    if len(t.split()) > 12:
        return False
    names = {n for p in personas.values() for n in p.names()} | {"raven"}
    for n in sorted(names, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(n)}\b", " ", t)
    t = " ".join(t.split())
    bare = " ".join(_FILLERS.sub(" ", t).split())
    return any(c.fullmatch(x) for c in _CHAT for x in {t, bare} if x)


def match(text: str, personas: dict[str, Persona]) -> Command | None:
    t = text.strip().lower().rstrip(".!?,")
    if _STOP.match(t):
        return Command("stop")
    if is_chitchat(text, personas):
        return Command("chat")
    if _HANDBACK.search(t):
        return Command("handback")
    if _OPEN.search(t) and len(t.split()) <= 9:
        return Command("open_terminal")
    if _LIST.search(t) and len(t.split()) <= 8:
        return Command("list")
    for pat in _SWITCH:
        m = re.match(pat, t)
        if m:
            p = find(m.group(1), personas)
            if p:
                return Command("switch", p)
    return None


def describe_list(personas: dict[str, Persona], active_id: str) -> str:
    parts = [f"{p.name}" + (" (active)" if p.id == active_id else "") for p in personas.values()]
    if len(parts) > 1:
        return "Available personas: " + ", ".join(parts[:-1]) + f", and {parts[-1]}."
    return "Available persona: " + parts[0] + "."
