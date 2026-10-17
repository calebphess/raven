"""Personas: name + voice + personality, as TOML. Built-ins ship in the package;
user/downloaded ones live in ~/.raven/personas/<id>/persona.toml (a "pack")."""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import urllib.request
import zipfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from . import config
from .interfaces import VoiceSpec

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

@dataclass
class Persona:
    id: str
    name: str
    description: str
    personality: str
    voice: VoiceSpec
    greeting: str = ""
    aliases: list[str] = field(default_factory=list)
    acks: list[str] = field(default_factory=list)  # instant spoken acknowledgements when work is handed off
    source: str = "builtin"  # builtin | installed

    def names(self) -> list[str]:
        return [n.lower() for n in [self.name, self.id, *self.aliases]]


def parse(text: str, pid: str, source: str = "builtin") -> Persona:
    d = tomllib.loads(text)
    v = d.get("voice", {})
    if "model" not in v:
        raise ValueError(f"persona {pid!r}: [voice] needs a `model`")
    params = {k: val for k, val in v.items() if k not in ("backend", "model", "effects")}
    return Persona(
        id=pid,
        name=d.get("name", pid.title()),
        description=d.get("description", ""),
        personality=d.get("personality", "").strip(),
        greeting=d.get("greeting", ""),
        aliases=list(d.get("aliases", [])),
        acks=list(d.get("acks", [])),
        voice=VoiceSpec(backend=v.get("backend", "piper"), model=v["model"],
                        params=params, effects=dict(v.get("effects", {}))),
        source=source,
    )


def _builtin() -> dict[str, Persona]:
    out = {}
    root = resources.files("raven2") / "personas_builtin"
    for f in sorted(root.iterdir(), key=lambda p: p.name):
        if f.name.endswith(".toml"):
            pid = f.name[:-5]
            out[pid] = parse(f.read_text(), pid, "builtin")
    return out


def _installed(personas_dir: Path) -> dict[str, Persona]:
    out = {}
    if personas_dir.is_dir():
        for p in sorted(personas_dir.iterdir()):
            f = p / "persona.toml" if p.is_dir() else p
            if f.suffix == ".toml" and f.exists():
                pid = p.name if p.is_dir() else p.stem
                try:
                    out[pid] = parse(f.read_text(), pid, "installed")
                except Exception as e:  # one bad pack must not break the rest
                    print(f"[raven] skipping persona {pid}: {e}", file=sys.stderr)
    return out


def load_all(personas_dir: Path | None = None) -> dict[str, Persona]:
    """Installed personas override built-ins with the same id."""
    return {**_builtin(), **_installed(personas_dir or config.PERSONAS_DIR)}


def find(query: str, personas: dict[str, Persona]) -> Persona | None:
    q = query.strip().lower().rstrip(".!?")
    for p in personas.values():
        if q in p.names():
            return p
    for p in personas.values():  # looser: "the butler", "dottie please"
        if any(n in q or q in n for n in p.names()):
            return p
    return None


DEFAULT_ID = "dottie"


# ── Active persona state ──────────────────────────────────────────────────────

def get_active_id(default: str = DEFAULT_ID) -> str:
    try:
        return json.loads(config.STATE_FILE.read_text()).get("persona", default)
    except Exception:
        return default


def set_active_id(pid: str) -> None:
    config.HOME.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(config.STATE_FILE.read_text())
    except Exception:
        state = {}
    state["persona"] = pid
    config.STATE_FILE.write_text(json.dumps(state))


# ── Install / remove ──────────────────────────────────────────────────────────

_SAFE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _validate_and_write(files: dict[str, bytes], pid: str, personas_dir: Path) -> Persona:
    if not _SAFE.match(pid):
        raise ValueError(f"bad persona id {pid!r}")
    if "persona.toml" not in files:
        raise ValueError("pack has no persona.toml")
    persona = parse(files["persona.toml"].decode(), pid, "installed")  # validates before touching disk
    dest = personas_dir / pid
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for rel, data in files.items():
        target = (dest / rel).resolve()
        if dest.resolve() not in target.parents:  # zip-slip guard
            raise ValueError(f"unsafe path in pack: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return persona


def install(source: str, personas_dir: Path | None = None) -> Persona:
    """source: a pack directory, a .toml file, a .zip path/URL, a persona.toml URL,
    or a pack name from the official index (packs/<name>/persona.toml)."""
    personas_dir = personas_dir or config.PERSONAS_DIR
    p = Path(source).expanduser()
    if p.is_dir():
        files = {str(f.relative_to(p)): f.read_bytes() for f in p.rglob("*") if f.is_file()}
        return _validate_and_write(files, p.name.lower(), personas_dir)
    if p.is_file() and p.suffix == ".toml":
        return _validate_and_write({"persona.toml": p.read_bytes()}, p.stem.lower(), personas_dir)
    if p.is_file() and p.suffix == ".zip":
        return _from_zip(p.read_bytes(), p.stem.lower(), personas_dir)
    if source.startswith(("http://", "https://")):
        data = urllib.request.urlopen(source, timeout=30).read()
        stem = Path(source.split("?")[0]).stem.lower()
        if source.split("?")[0].endswith(".zip"):
            return _from_zip(data, stem, personas_dir)
        pid = Path(source.split("?")[0]).parent.name.lower() if stem == "persona" else stem
        return _validate_and_write({"persona.toml": data}, pid, personas_dir)
    # bare name: download <packs base>/<name>.zip
    from . import packs

    packs.install_pack(source.lower())
    return load_all()[source.lower()]


def _from_zip(data: bytes, pid: str, personas_dir: Path) -> Persona:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in z.namelist() if not n.endswith("/")]
    prefix = ""
    for n in names:
        if n.endswith("persona.toml"):
            prefix = n[: -len("persona.toml")]
            break
    files = {n[len(prefix):]: z.read(n) for n in names if n.startswith(prefix)}
    return _validate_and_write(files, pid, personas_dir)


def remove(pid: str, personas_dir: Path | None = None) -> bool:
    d = (personas_dir or config.PERSONAS_DIR) / pid
    if d.is_dir():
        shutil.rmtree(d)
        return True
    f = d.with_suffix(".toml")
    if f.exists():
        f.unlink()
        return True
    return False
