"""Startup dialogs for the packaged app (a Finder-launched app has no terminal to print errors to)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import preflight
from .ui import THEME, _label, app_pixmap, apply_theme, make_icon

_DIALOG_QSS = f"""
    QDialog {{ background: {THEME['bg']}; }}
    QLabel#dlgTitle {{ color: {THEME['text']}; font-size: 19px; font-weight: 700; }}
    QLabel#dlgBody {{ color: {THEME['muted']}; font-size: 14px; }}
    QFrame#codeBox {{ background: {THEME['surface']}; border: 1px solid {THEME['border']}; border-radius: 10px; }}
    QLabel#code {{ color: {THEME['accent']}; font-family: Menlo, monospace; font-size: 12px; }}
    QPushButton {{ background: {THEME['surface']}; border: 1px solid {THEME['border']}; border-radius: 10px;
        padding: 10px 18px; color: {THEME['text']}; font-size: 13px; font-weight: 600; }}
    QPushButton:hover {{ background: {THEME['elevated']}; }}
    QPushButton#primary {{ background: {THEME['accent']}; color: {THEME['on_accent']}; border: none; }}
    QPushButton#primary:hover {{ background: #f0d083; }}
"""


class SetupDialog(QDialog):
    """A calm full-width explanation with a few actions. `result_key` says which button closed it."""

    def __init__(self, title: str, body: str, code: str | None = None, buttons=(), parent=None):
        super().__init__(parent)
        apply_theme(QGuiApplication.instance())
        self.setWindowTitle("Raven")
        self.setWindowIcon(make_icon())
        self.setMinimumWidth(580)
        self.result_key = "quit"
        self.buttons: dict[str, QPushButton] = {}
        logo = QLabel()
        logo.setPixmap(app_pixmap(56))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 26, 28, 22)
        lay.setSpacing(14)
        lay.addWidget(logo)
        lay.addWidget(_label(title, "dlgTitle"))
        lay.addWidget(_label(body, "dlgBody"))
        if code:
            box = QFrame()
            box.setObjectName("codeBox")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(14, 12, 14, 12)
            c = _label(code, "code")
            c.setTextInteractionFlags(Qt.TextSelectableByMouse)
            bl.addWidget(c)
            lay.addWidget(box)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        for key, label, primary in buttons:
            b = QPushButton(label)
            b.setMinimumWidth(b.fontMetrics().horizontalAdvance(label) + 52)  # never clip a label
            if primary:
                b.setObjectName("primary")
                b.setDefault(True)
            b.clicked.connect(lambda _=False, k=key: self._pressed(k))
            self.buttons[key] = b
            row.addWidget(b)
        lay.addLayout(row)
        self.setStyleSheet(_DIALOG_QSS)

    def _pressed(self, key: str) -> None:
        if key == "copy":  # stays open
            QGuiApplication.clipboard().setText(preflight.INSTALL_COMMAND)
            self.buttons["copy"].setText("Copied ✓")
            return
        if key == "guide":
            QDesktopServices.openUrl(QUrl(preflight.INSTALL_URL))
            return
        self.result_key = key
        self.accept()


def claude_missing_dialog(parent=None) -> SetupDialog:
    return SetupDialog(
        "Install Claude Code first",
        "Raven does its work through Claude Code, Anthropic's coding agent, running on your Mac. It isn't "
        "installed yet. Run this in Terminal (or follow the install guide), then come back and check again.",
        code=preflight.INSTALL_COMMAND,
        buttons=(("quit", "Quit", False), ("guide", "Open install guide", False), ("copy", "Copy command", False),
                 ("again", "Check again", True)),
        parent=parent)


def models_missing_dialog(missing: list[str], models_dir: str, parent=None) -> SetupDialog:
    return SetupDialog(
        "Raven's voice models aren't installed",
        "Raven runs its voice and conversation locally, so it needs these downloaded first:\n\n• "
        + "\n• ".join(missing) + f"\n\nThey go in {models_dir}.",
        buttons=(("quit", "Quit", True),),
        parent=parent)
