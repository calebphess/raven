"""Simple desktop UI: menubar icon + window with the conversation, live worker status, typed input, a persona
picker, and a button that opens the live Claude Code session in a terminal (taking it over from Raven).

The window is only a view/controller over `Assistant`; closing it hides it to the menubar and the push-to-talk
hotkey keeps working."""

from __future__ import annotations

import html
import threading

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget)

from .core import Assistant, StartupError
from .status import Status

_COLORS = {"working": "#d9a400", "waiting": "#b04fd0", "done": "#2e9e4f", "error": "#d03a3a",
           "manual": "#2f8fd0", "idle": "#8a8a8a"}


def render_entries(entries: list[tuple[str, str]]) -> str:
    """[(speaker, text)] -> HTML. Consecutive lines from one speaker become one paragraph, so a reply that was
    spoken in several chunks reads as a single message."""
    merged: list[list[str]] = []
    for who, text in entries:
        if merged and merged[-1][0] == who and who not in ("!", "·"):
            merged[-1][1] += " " + text
        else:
            merged.append([who, text])
    out = []
    for who, text in merged:
        t = html.escape(text)
        if who == "you":
            out.append(f'<p style="margin:6px 0"><b style="color:#2f8fd0">You</b><br>{t}</p>')
        elif who == "!":
            out.append(f'<p style="margin:6px 0;color:#d03a3a">{t}</p>')
        elif who == "·":
            out.append(f'<p style="margin:6px 0;color:#8a8a8a"><i>{t}</i></p>')
        else:
            out.append(f'<p style="margin:6px 0"><b>{html.escape(who)}</b><br>{t}</p>')
    return "".join(out)


def parse_log(line: str) -> tuple[str, str]:
    """Assistant log line -> (speaker, text). 'you: hi' -> ('you', 'hi'); '[worker error] x' -> ('!', ...)."""
    if line.startswith("[") and ("error" in line[:20] or "fallback" in line[:20]):
        return "!", line
    who, sep, rest = line.partition(": ")
    if sep and len(who) <= 24 and "[" not in who:
        return who, rest
    return "·", line


class Bridge(QObject):
    line = Signal(str)
    ready = Signal()
    failed = Signal(str)
    notice = Signal(str)


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#1f1f24"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setPen(QColor("#ffffff"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(36)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "R")
    p.end()
    return QIcon(pm)


class RavenWindow(QWidget):
    def __init__(self, asst: Assistant, bridge: Bridge):
        super().__init__()
        self.asst, self.bridge = asst, bridge
        self.entries: list[tuple[str, str]] = []
        self.setWindowTitle("Raven")
        self.resize(460, 620)
        self.setWindowIcon(make_icon())

        self.persona = QComboBox()
        self.status = QLabel("Loading models…")
        self.status.setStyleSheet(f"color:{_COLORS['idle']}; font-weight:600;")
        top = QHBoxLayout()
        top.addWidget(self.persona)
        top.addWidget(self.status, 1)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a message to Raven…")
        self.send = QPushButton("Send")
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.send)

        self.terminal_btn = QPushButton("Open Claude session in Terminal")
        self.terminal_btn.setMinimumHeight(36)
        self.terminal_btn.setToolTip("Pauses Raven and opens this Claude Code session in a terminal so you can "
                                     "drive it by hand. Click again to hand it back.")

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)
        lay.addWidget(self.terminal_btn)

        self.input.returnPressed.connect(self._send)
        self.send.clicked.connect(self._send)
        self.terminal_btn.clicked.connect(self._toggle_manual)
        self.persona.activated.connect(self._pick_persona)
        bridge.line.connect(self.on_line)
        bridge.ready.connect(self._on_ready)
        bridge.notice.connect(lambda m: self._add("!", m))
        self._set_enabled(False)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(150)

    # ── conversation ──
    def on_line(self, line: str) -> None:
        self._add(*parse_log(line))

    def _add(self, who: str, text: str) -> None:
        self.entries.append((who, text))
        self.view.setHtml(render_entries(self.entries))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            threading.Thread(target=self.asst.handle_text, args=(text,), daemon=True).start()

    # ── persona ──
    def _fill_personas(self) -> None:
        self.persona.blockSignals(True)
        self.persona.clear()
        for r in self.asst.list_personas():
            self.persona.addItem(r["name"], r["id"])
        self.persona.setCurrentIndex(max(0, self.persona.findData(self.asst.persona.id)))
        self.persona.blockSignals(False)

    def _pick_persona(self, _i: int) -> None:
        name = self.persona.currentText()

        def run():
            try:
                self.asst.set_persona(name)
            except Exception as e:
                self.bridge.notice.emit(str(e))
        threading.Thread(target=run, daemon=True).start()

    # ── manual takeover ──
    def _toggle_manual(self) -> None:
        manual = self.asst.manual
        self.terminal_btn.setEnabled(False)

        def run():
            try:
                self.asst.hand_back() if manual else self.asst.open_manual_session()
            except Exception as e:
                self.bridge.notice.emit(str(e))
        threading.Thread(target=run, daemon=True).start()

    # ── state ──
    def _set_enabled(self, on: bool) -> None:
        for w in (self.input, self.send, self.persona, self.terminal_btn):
            w.setEnabled(on)

    def _on_ready(self) -> None:
        self._fill_personas()
        self._set_enabled(True)
        self.input.setFocus()

    def refresh(self) -> None:
        st = self.asst.status
        if st is None:
            return
        text = st.text()[1].removeprefix("Raven: ")
        key = st.state if not st.overlay else "idle"
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{_COLORS.get(key, _COLORS['idle'])}; font-weight:600;")
        manual = self.asst.manual
        self.terminal_btn.setText("Hand back to Raven" if manual else "Open Claude session in Terminal")
        if self.input.isEnabled() or self.persona.count():
            self.terminal_btn.setEnabled(True)
            self.input.setEnabled(not manual)
            self.send.setEnabled(not manual)
        cur = self.persona.findData(self.asst.persona.id)
        if cur >= 0 and cur != self.persona.currentIndex() and not self.persona.view().isVisible():
            self.persona.blockSignals(True)
            self.persona.setCurrentIndex(cur)
            self.persona.blockSignals(False)

    def closeEvent(self, e) -> None:  # hide to the menubar; the hotkey keeps working
        e.ignore()
        self.hide()


def run_ui(asst: Assistant, enable_ptt: bool = True) -> int:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Raven")
    bridge = Bridge()
    asst.log = bridge.line.emit
    asst.status = asst.status or Status(enabled=False)  # state only; the window draws it
    win = RavenWindow(asst, bridge)

    tray = QSystemTrayIcon(make_icon(), app)
    menu = QMenu()
    show = QAction("Show Raven")
    show.triggered.connect(lambda: (win.show(), win.raise_(), win.activateWindow()))
    term = QAction("Open Claude session in Terminal / Hand back")
    term.triggered.connect(win._toggle_manual)
    quit_ = QAction("Quit")
    quit_.triggered.connect(app.quit)
    for a in (show, term, quit_):
        menu.addAction(a)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: show.trigger() if reason == QSystemTrayIcon.Trigger else None)
    tray.show()
    win.show()

    def boot():
        try:
            asst.startup()
        except StartupError as e:
            bridge.failed.emit(str(e))
            return
        bridge.ready.emit()
        if enable_ptt:
            from . import ptt

            ptt.run(asst)  # global hotkey; works with the window closed

    def fail(msg: str) -> None:
        QMessageBox.critical(win, "Raven couldn't start", msg)
        app.exit(1)

    bridge.failed.connect(fail)
    threading.Thread(target=boot, daemon=True, name="boot").start()
    return app.exec()
