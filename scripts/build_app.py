"""Build dist/Raven.app (PyInstaller) and ad-hoc sign it. Run from the project venv: python scripts/build_app.py

Models are NOT bundled (they are large); the app checks for them on launch and says what is missing.

Safety: refuses to run while Raven.app is open (replacing the files under a running app can crash it), and builds in
a temporary folder, swapping the finished, signed app into dist/ at the end, so an interrupted or failed build never
leaves you without an app. Use --force to build while Raven is running anyway."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist" / "Raven.app"
STAGE = ROOT / "build" / "stage"


def running_copies(app: Path = APP) -> list[str]:
    """Processes running out of `app` (the app itself and its helpers)."""
    out = subprocess.run(["ps", "-Ao", "pid,comm"], capture_output=True, text=True).stdout
    prefix = str(app) + "/Contents/MacOS/"
    return [line.strip() for line in out.splitlines() if prefix in line]


def run(*cmd):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


def main(argv: list[str]) -> int:
    if "--force" not in argv and (live := running_copies()):
        print("Raven is running from dist/Raven.app; replacing it under a running app can crash it.\n"
              "Quit Raven and run this again (or pass --force).\n  " + "\n  ".join(live), file=sys.stderr)
        return 2
    for need in (ROOT / "packaging" / "Raven.icns", ROOT / "raven2" / "assets" / "icon.png",
                 ROOT / "raven2" / "assets" / "logo.png"):
        if not need.exists():
            print(f"missing {need}; run scripts/make_icon.py first", file=sys.stderr)
            return 1
    shutil.rmtree(STAGE, ignore_errors=True)
    staged = STAGE / "Raven.app"
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", STAGE,
        "--workpath", "build/pyinstaller", "packaging/Raven.spec")
    # Ad-hoc signature: keeps macOS permission grants (microphone, input monitoring) attached to this app.
    run("codesign", "--force", "--deep", "--sign", "-", staged)
    run("codesign", "--verify", "--deep", "--strict", staged)
    if "--force" not in argv and (live := running_copies()):   # it may have been opened during the build
        print("Raven was opened while building; leaving the new app at", staged, file=sys.stderr)
        return 2
    shutil.rmtree(APP, ignore_errors=True)                    # the swap is the only moment dist/Raven.app is absent
    APP.parent.mkdir(exist_ok=True)
    os.replace(staged, APP)
    run("codesign", "--verify", "--deep", "--strict", APP)
    kb = int(subprocess.run(["du", "-sk", str(APP)], capture_output=True, text=True).stdout.split()[0])
    print(f"\nBuilt {APP}  ({kb / 1024:.0f} MB on disk)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
