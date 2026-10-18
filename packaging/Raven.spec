# -*- mode: python ; coding: utf-8 -*-
# Build with: python scripts/build_app.py      (output: dist/Raven.app)
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent

datas, binaries, hidden = [], [], []
# Libraries that ship native code or data files next to their Python (models' runtimes, audio, keyboard...).
for pkg in ("llama_cpp", "piper", "faster_whisper", "ctranslate2", "onnxruntime", "_sounddevice_data", "av",
            "certifi", "claude_agent_sdk", "pynput"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
# Backends are named in config and imported lazily by string, so the freezer can't see them.
hidden += collect_submodules("raven2")
hidden += ["sounddevice", "scipy.signal", "tomli", "Quartz", "objc", "AppKit", "Foundation", "CoreFoundation"]
datas += [(str(ROOT / "raven2" / "personas_builtin"), "raven2/personas_builtin"),
          (str(ROOT / "raven2" / "assets"), "raven2/assets")]

a = Analysis([str(ROOT / "packaging" / "entry.py")], pathex=[str(ROOT)], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "IPython", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Raven", console=False,
          icon=str(ROOT / "packaging" / "Raven.icns"))
coll = COLLECT(exe, a.binaries, a.datas, name="Raven")
app = BUNDLE(
    coll, name="Raven.app", icon=str(ROOT / "packaging" / "Raven.icns"), bundle_identifier="com.raven.voice",
    info_plist={
        "CFBundleName": "Raven", "CFBundleDisplayName": "Raven",
        "CFBundleShortVersionString": "0.2.0", "CFBundleVersion": "1",
        "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "10.15",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSMicrophoneUsageDescription": "Raven listens for your voice while you hold the push-to-talk key.",
        "NSAppleEventsUsageDescription": "Raven opens your Claude Code session in Terminal when you ask it to.",
    },
)
