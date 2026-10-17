"""Spoken yes/no parsing for permission requests."""

from __future__ import annotations

import re

_DENY = re.compile(r"\b(no|nope|deny|stop|cancel|don'?t|do not)\b")
_ALWAYS = re.compile(r"\b(always|yes to all|allow all|all of them)\b")
_ALLOW = re.compile(r"\b(yes|yeah|yep|sure|ok|okay|go ahead|do it|allow|approve)\b")


def parse_decision(text: str) -> str | None:
    """'deny' | 'always' | 'allow' | None. Deny is checked first: a wrong deny just re-prompts,
    a wrong allow runs the command. Never auto-allow on silence or unclear speech."""
    t = text.lower()
    if _DENY.search(t):
        return "deny"
    if _ALWAYS.search(t):
        return "always"
    if _ALLOW.search(t):
        return "allow"
    return None
