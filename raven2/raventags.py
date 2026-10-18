"""<raven>…</raven> blocks: text Claude wants spoken verbatim through Raven's current persona."""

from __future__ import annotations

import re

# An unclosed tag (truncated output) still counts: everything after <raven> is spoken.
_TAG = re.compile(r"<raven>(.*?)(?:</raven>|$)", re.S | re.I)


def split_raven(text: str) -> tuple[list[str], str]:
    """-> (spoken blocks, everything else). The rest is log-only prose the user is not expected to read."""
    spoken = [m.group(1).strip() for m in _TAG.finditer(text)]
    return [s for s in spoken if s], _TAG.sub("", text).strip()


def strip_tags(text: str) -> str:
    """Plain text with the tags removed but their contents kept."""
    return re.sub(r"</?raven>", "", text, flags=re.I).strip()
