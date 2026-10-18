"""Startup checks for the packaged app: is Claude Code installed, and are Raven's local models present?

A Finder-launched app does not inherit your shell's PATH (so `~/.local/bin`, Homebrew and npm globals are
invisible), so Claude Code is looked up in the usual install locations and, last, through a login shell."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

INSTALL_COMMAND = "curl -fsSL https://claude.ai/install.sh | bash"
INSTALL_URL = "https://code.claude.com/docs/en/setup"


@dataclass
class ClaudeInfo:
    path: str
    version: str


def candidate_dirs(home: Path | None = None) -> list[str]:
    h = home or Path.home()
    dirs = [h / ".local/bin", h / ".claude/local", Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
            h / ".npm-global/bin", h / ".bun/bin", h / ".volta/bin", h / ".yarn/bin", Path("/usr/bin")]
    dirs += [Path(p) for p in sorted(glob.glob(str(h / ".nvm/versions/node/*/bin")), reverse=True)]
    return [str(d) for d in dirs]


def _version(path: str) -> str | None:
    """Run `claude --version`; a missing, broken or non-executable file is not an install."""
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").strip()
    return out.splitlines()[0] if r.returncode == 0 and out else None


def _login_shell_lookup(env: dict) -> str | None:
    shell = env.get("SHELL") or "/bin/zsh"
    try:
        r = subprocess.run([shell, "-ilc", "command -v claude"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed(r.stdout.splitlines()):  # interactive shells can print noise first
        line = line.strip()
        if line.startswith("/") and line.endswith("claude"):
            return line
    return None


def find_claude(env: dict | None = None, home: Path | None = None, login_shell: bool = True) -> ClaudeInfo | None:
    """First working `claude`: $RAVEN_CLAUDE, then PATH, then common install dirs, then a login shell."""
    env = dict(os.environ if env is None else env)
    paths: list[str] = []
    if env.get("RAVEN_CLAUDE"):
        paths.append(env["RAVEN_CLAUDE"])
    hit = shutil.which("claude", path=env.get("PATH", ""))
    if hit:
        paths.append(hit)
    paths += [str(Path(d) / "claude") for d in candidate_dirs(home)]
    seen: set[str] = set()
    for p in paths:
        if p in seen or not os.access(p, os.X_OK):
            continue
        seen.add(p)
        if (v := _version(p)):
            return ClaudeInfo(p, v)
    if login_shell and (p := _login_shell_lookup(env)) and p not in seen and (v := _version(p)):
        return ClaudeInfo(p, v)
    return None


def missing_models(profile: dict | None = None) -> list[str]:
    """Human-readable names of local models that are not installed yet (empty = ready to run)."""
    from . import config

    profile = profile or config.load_profile()
    missing: list[str] = []
    llm = profile["llm"]["model"]
    try:
        config.resolve_model(llm)
    except FileNotFoundError:
        missing.append(f"language model ({llm})")
    stt = profile["stt"]["model"]
    if not glob.glob(str(config.MODELS_DIR / "whisper" / f"models--*faster-whisper-{stt}*" / "snapshots" / "*")):
        missing.append(f"speech-to-text model (whisper {stt})")
    return missing
