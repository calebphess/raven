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


def match(text: str, personas: dict[str, Persona]) -> Command | None:
    t = text.strip().lower().rstrip(".!?,")
    if _STOP.match(t):
        return Command("stop")
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
