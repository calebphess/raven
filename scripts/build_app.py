"""Build dist/Raven.app (PyInstaller) and ad-hoc sign it. Run from the project venv: python scripts/build_app.py

Models are NOT bundled (they are large); the app checks for them on launch and says what is missing."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*cmd):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


def main() -> int:
    for need in (ROOT / "packaging" / "Raven.icns", ROOT / "raven2" / "assets" / "icon.png"):
        if not need.exists():
            print(f"missing {need}; run scripts/make_icon.py first", file=sys.stderr)
            return 1
    shutil.rmtree(ROOT / "dist" / "Raven.app", ignore_errors=True)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", "dist",
        "--workpath", "build/pyinstaller", "packaging/Raven.spec")
    app = ROOT / "dist" / "Raven.app"
    # Ad-hoc signature: keeps macOS permission grants (microphone, input monitoring) attached to this app.
    run("codesign", "--force", "--deep", "--sign", "-", app)
    run("codesign", "--verify", "--deep", "--strict", app)
    kb = int(subprocess.run(["du", "-sk", str(app)], capture_output=True, text=True).stdout.split()[0])  # real size
    print(f"\nBuilt {app}  ({kb / 1024:.0f} MB on disk)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
