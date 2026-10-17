"""Entry point of the packaged Raven.app (also runnable in development: `python -m raven2.gui_main`).

Order: set up the environment -> check Claude Code is installed -> check the local models -> pick the project
folder Claude works in -> run the UI. Each failed check is a window, not a log line."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import config, preflight


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
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
    except Exception:
        pass  # not macOS, or pyobjc missing: the key listener will simply not receive events


def ensure_claude(app) -> preflight.ClaudeInfo | None:
    """Loop until Claude Code is found or the user quits."""
    from .dialogs import claude_missing_dialog

    while True:
        info = preflight.find_claude()
        if info:
            return info
        dlg = claude_missing_dialog()
        dlg.exec()
        if dlg.result_key != "again":
            return None


def choose_folder(parent=None, start: str | None = None) -> str | None:
    from PySide6.QtWidgets import QFileDialog

    d = QFileDialog.getExistingDirectory(parent, "Choose the folder Claude should work in", start or str(Path.home()))
    return d or None


def ensure_project_dir(app) -> str | None:
    saved = config.read_state().get("project_dir")
    if saved and os.path.isdir(saved):
        return saved
    d = choose_folder()
    if d:
        config.update_state(project_dir=d)
    return d


def change_folder(win) -> None:
    """Pick a new project folder and restart: a Claude session belongs to one folder."""
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    d = choose_folder(win, win.project_dir)
    if not d or d == win.project_dir:
        return
    config.update_state(project_dir=d)
    args = [] if getattr(sys, "frozen", False) else sys.argv
    QProcess.startDetached(sys.executable, args)
    QApplication.instance().quit()


def main() -> int:
    setup_environment()
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
        models_missing_dialog(missing, str(config.MODELS_DIR)).exec()
        return 1
    project = ensure_project_dir(app)
    if project is None:
        return 0
    request_input_monitoring()
    asst = builder.build(worker="claude", cwd=project, claude_path=info.path)
    return run_ui(asst, project_dir=project, on_change_folder=change_folder)


if __name__ == "__main__":
    raise SystemExit(main())
