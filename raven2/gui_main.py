"""Entry point of the packaged Raven.app (also runnable in development: `python -m raven2.gui_main`).

Order: set up the environment -> check Claude Code is installed -> check the local models -> start Claude in
the home folder -> run the UI (File > New Project / Open Project move it elsewhere). Each failed check is a window, not a log line."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from . import config, preflight

log = logging.getLogger("raven")


def setup_logging() -> Path:
    """A Finder-launched app has no terminal, so write what it does (and any crash) to ~/.raven/logs/app.log."""
    path = config.HOME / "logs" / "app.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(path), level=logging.INFO, force=True,
                        format="%(asctime)s %(levelname)s %(message)s")

    def crash(kind, value, tb):
        log.critical("uncaught exception", exc_info=(kind, value, tb))
        sys.__excepthook__(kind, value, tb)

    sys.excepthook = crash
    return path


def setup_environment() -> None:
    """A Finder-launched app gets a minimal PATH and none of your shell's settings."""
    os.environ.pop("RAVEN", None)  # the V1 hooks gate on this; never inherit it
    extra = [d for d in preflight.candidate_dirs() if os.path.isdir(d)]
    current = os.environ.get("PATH", "").split(":")
    os.environ["PATH"] = ":".join(extra + [p for p in current if p and p not in extra])
    if getattr(sys, "frozen", False):  # a frozen Python can't find the system CA bundle on its own
        try:
            import certifi

            os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        except Exception:
            pass
    # Models are verified present before the UI starts; never let a model library stall on the network.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")


def request_input_monitoring() -> None:
    """Ask macOS for Input Monitoring (needed for the global push-to-talk key). Safe to call repeatedly."""
    if os.environ.get("RAVEN_NO_PTT"):
        return
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
    except Exception:
        pass  # not macOS, or pyobjc missing: the key listener will simply not receive events


def diagnostics() -> dict:
    """Whether the bundled native pieces load. Logged at startup: in a frozen app, a missing library is the
    most likely failure and there is no terminal to show it."""
    out: dict = {}
    try:
        import sounddevice

        out["audio_devices"] = len(sounddevice.query_devices())
    except Exception as e:
        out["audio_devices"] = f"FAILED {type(e).__name__}: {e}"
    try:
        import pynput.keyboard  # noqa: F401

        out["pynput"] = "ok"
    except Exception as e:
        out["pynput"] = f"FAILED {type(e).__name__}: {e}"
    try:
        import Quartz

        out["input_monitoring_granted"] = bool(Quartz.CGPreflightListenEventAccess())
    except Exception as e:
        out["input_monitoring_granted"] = f"unknown ({type(e).__name__})"
    return out


def ensure_claude(app) -> preflight.ClaudeInfo | None:
    """Loop until Claude Code is found or the user quits."""
    from .dialogs import claude_missing_dialog

    while True:
        info = preflight.find_claude()
        if info:
            log.info("claude code found: %s (%s)", info.path, info.version)
            return info
        log.warning("claude code not found; showing the install dialog")
        dlg = claude_missing_dialog()
        dlg.exec()
        if dlg.result_key != "again":
            return None


def main() -> int:
    setup_environment()
    setup_logging()
    log.info("starting Raven (frozen=%s) python=%s", getattr(sys, "frozen", False), sys.version.split()[0])
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Raven")
    from . import app as builder
    from .dialogs import models_missing_dialog
    from .ui import apply_theme, make_icon, run_ui

    apply_theme(app)
    app.setWindowIcon(make_icon())

    info = ensure_claude(app)
    if info is None:
        return 0
    missing = preflight.missing_models()
    if missing:
        log.warning("models missing: %s", missing)
        models_missing_dialog(missing, str(config.MODELS_DIR)).exec()
        return 1
    project = str(Path.home())  # always start in the home folder; File > New/Open Project switches
    log.info("project folder: %s", project)
    log.info("diagnostics: %s", diagnostics())
    request_input_monitoring()
    asst = builder.build(worker="claude", cwd=project, claude_path=info.path)
    return run_ui(asst, project_dir=project)


if __name__ == "__main__":
    raise SystemExit(main())
