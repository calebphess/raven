"""
Claude Code hook (PreToolUse and Stop) — speaks <raven>…</raven> blocks from
the assistant's output via ElevenLabs.

PreToolUse speaks the plan as soon as it is written, before the tool runs.
Stop speaks the final synopsis, preferring `last_assistant_message` from the
hook input and falling back to the transcript.

Does nothing unless RAVEN=1 (set by the `raven` launcher), so plain Claude
Code sessions are unaffected.

A per-session state file tracks which transcript lines were already read so
hooks never speak the same block twice.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

from .config import ELEVENLABS_API_KEY, RAVEN_TAG
from .tts import speak

_STATE_DIR = Path.home() / ".raven"
_TAG_RE = re.compile(rf"<{RAVEN_TAG}>(.*?)</{RAVEN_TAG}>", re.DOTALL | re.IGNORECASE)


# ── State tracking ─────────────────────────────────────────────────────────────

def _state_file(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id) or "default"
    return _STATE_DIR / f"speak_state_{safe}.json"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_state(path: Path, state: dict) -> None:
    try:
        _STATE_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(state))
    except Exception:
        pass


def _prune_old_state(max_age_s: float = 86_400) -> None:
    try:
        cutoff = time.time() - max_age_s
        for f in _STATE_DIR.glob("speak_state_*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
    except Exception:
        pass


# ── Parsing ────────────────────────────────────────────────────────────────────

def raven_blocks(text: str) -> list[str]:
    """Return the cleaned contents of every <raven> block in text."""
    out = []
    for m in _TAG_RE.finditer(text or ""):
        cleaned = " ".join(m.group(1).split())
        if cleaned:
            out.append(cleaned)
    return out


def _is_real_user_message(entry: dict) -> bool:
    if entry.get("type") != "user":
        return False
    content = entry.get("message", {}).get("content", [])
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") != "tool_result" for b in content)
    return bool(content)


def _entry_text(entry: dict) -> str:
    if entry.get("type") == "assistant":
        msg = entry.get("message", {})
    elif entry.get("role") == "assistant":
        msg = entry
    else:
        return ""
    content = msg.get("content", "")
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content) if content else ""


def _new_blocks_from_transcript(transcript_path: str, state_path: Path) -> list[str]:
    """Return <raven> blocks added since the last call for this session."""
    try:
        with open(transcript_path) as f:
            raw_lines = f.readlines()
    except OSError:
        return []

    state = _load_state(state_path)
    if state.get("transcript_path") == transcript_path:
        start = state.get("last_line_index", 0)
    else:
        # First call for this transcript: start after the last real user message.
        start = 0
        for i, raw in enumerate(raw_lines):
            try:
                if _is_real_user_message(json.loads(raw)):
                    start = i + 1
            except Exception:
                continue

    blocks: list[str] = []
    for raw in raw_lines[start:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        blocks.extend(raven_blocks(_entry_text(entry)))

    _save_state(state_path, {"transcript_path": transcript_path, "last_line_index": len(raw_lines)})
    return blocks


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    if os.environ.get("RAVEN") != "1":
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)

    if not ELEVENLABS_API_KEY:
        print("[raven] Warning: ELEVENLABS_API_KEY not set", file=sys.stderr)
        sys.exit(0)

    transcript_path = data.get("transcript_path")
    state_path = _state_file(data.get("session_id", ""))
    _prune_old_state()

    blocks: list[str] = []
    if transcript_path:
        # The transcript may still be flushing; retry briefly.
        for attempt in range(3):
            blocks = _new_blocks_from_transcript(transcript_path, state_path)
            if blocks:
                break
            if attempt < 2:
                time.sleep(0.15)

    # Stop only: if the transcript had nothing new, use the final message
    # from the hook input, skipping it if it was already spoken.
    if not blocks and data.get("hook_event_name") == "Stop":
        blocks = raven_blocks(data.get("last_assistant_message", ""))

    state = _load_state(state_path)
    text = " ".join(blocks)
    if text and text == state.get("last_spoken"):
        sys.exit(0)
    if text:
        state["last_spoken"] = text
        _save_state(state_path, state)

    if text:
        speak(text)


if __name__ == "__main__":
    main()
