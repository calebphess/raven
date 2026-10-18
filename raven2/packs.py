"""Persona packs: one zip per persona (persona.toml + voice files), hosted anywhere static
(S3, GitHub releases, a folder). Layout at the host:  <base>/<id>.zip  and optional <base>/index.json
({"<id>": {"sha256": "..."}}) for integrity checks. Base: RAVEN_PACKS_URL, or `packs_url` in config.toml."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import config

DEFAULT_BASE = "https://github.com/calebphess/raven/releases/download/packs"
_REPO_DIST = Path(__file__).resolve().parent.parent / "dist" / "packs"


class PackError(RuntimeError):
    pass


def base_url() -> str:
    url = os.environ.get("RAVEN_PACKS_URL") or config.load_file().get("packs_url")
    if url:
        return url.rstrip("/")
    return _REPO_DIST.as_uri() if _REPO_DIST.is_dir() else DEFAULT_BASE  # dev checkout works offline


def _open(url: str, timeout: float = 20):
    return urllib.request.urlopen(url, timeout=timeout)


def _fetch(url: str, dest: Path, label: str, tries: int = 3) -> None:
    for attempt in range(1, tries + 1):
        try:
            with _open(url) as r, open(dest, "wb") as f:
                total, got, last = int(r.headers.get("Content-Length") or 0), 0, -1
                while chunk := r.read(1 << 16):
                    f.write(chunk)
                    got += len(chunk)
                    pct = got * 100 // total if total else 0
                    if pct != last:
                        last = pct
                        print(f"\rDownloading persona: {label} {pct}%   " if total else f"\rDownloading persona: {label}...",
                              end="", file=sys.stderr, flush=True)
            print(file=sys.stderr)
            return
        except Exception as e:
            err = e
            time.sleep(attempt)
    print(file=sys.stderr)
    raise PackError(f"couldn't download persona {label} ({type(err).__name__}); check the connection and try again")


def _expected_sha(pid: str, base: str) -> str | None:
    try:
        with _open(f"{base}/index.json", timeout=10) as r:
            return json.load(r).get(pid, {}).get("sha256")
    except Exception:
        return None  # index is optional


def install_pack(pid: str, name: str | None = None) -> None:
    """Download <base>/<pid>.zip and install it."""
    from . import personas

    base, label = base_url(), name or pid.title()
    with tempfile.TemporaryDirectory() as td:
        z = Path(td) / f"{pid}.zip"
        _fetch(f"{base}/{pid}.zip", z, label)
        want = _expected_sha(pid, base)
        if want and hashlib.sha256(z.read_bytes()).hexdigest() != want:
            raise PackError(f"persona {label} failed its integrity check; not installing")
        print(f"Installing persona: {label}", file=sys.stderr, flush=True)
        personas.install(str(z))
