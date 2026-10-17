"""Build hostable persona packs into dist/packs/: <id>.zip (persona.toml + voice files) and index.json.

Upload dist/packs/* to any static host (S3 etc.) and point RAVEN_PACKS_URL (or `packs_url` in
~/.raven/config.toml) at it. Voices must already be present under ~/.raven/models.
Usage: python scripts/build_packs.py [id ...]
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from raven2 import config, personas as P  # noqa: E402

OUT = ROOT / "dist" / "packs"


def sources() -> dict[str, Path]:
    srcs = {f.stem: f for f in (ROOT / "raven2" / "personas_builtin").glob("*.toml")}
    srcs.update({d.name: d / "persona.toml" for d in (ROOT / "packs").iterdir() if (d / "persona.toml").exists()})
    return srcs


def main(ids: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    index, srcs = {}, sources()
    for pid, toml in sorted(srcs.items()):
        if ids and pid not in ids:
            continue
        persona = P.parse(toml.read_text(), pid)
        onnx = next(iter(config.MODELS_DIR.rglob(f"{persona.voice.model}.onnx")), None)
        if onnx is None or not onnx.with_suffix(".onnx.json").exists():
            print(f"skip {pid}: voice {persona.voice.model} not found under {config.MODELS_DIR}")
            continue
        try:  # never package a truncated/corrupt voice
            from piper import PiperVoice

            v = PiperVoice.load(str(onnx))
            if not sum(len(c.audio_int16_array) for c in v.synthesize("Test.")):
                raise RuntimeError("no audio produced")
        except Exception as e:
            print(f"skip {pid}: voice {onnx.name} failed to load/synthesize ({type(e).__name__}: {e})")
            continue
        z = OUT / f"{pid}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(toml, "persona.toml")
            zf.write(onnx, onnx.name)
            zf.write(onnx.with_suffix(".onnx.json"), onnx.name + ".json")
        index[pid] = {"sha256": hashlib.sha256(z.read_bytes()).hexdigest(), "bytes": z.stat().st_size,
                      "name": persona.name, "description": persona.description}
        print(f"built {z.name}  {z.stat().st_size / 1e6:.1f} MB")
    old = json.loads((OUT / "index.json").read_text()) if (OUT / "index.json").exists() else {}
    (OUT / "index.json").write_text(json.dumps({**old, **index}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
