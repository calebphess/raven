"""Desktop UI: a calm, modern chat window over `Assistant`, plus a menubar icon.

Navy surfaces with the app icon's gold as the one accent. The window shows the conversation as bubbles, a live
status capsule for the background Claude, a persona picker, a typed-input composer, and a button that opens the
live Claude Code session in a terminal (taking it over from Raven).

Closing the window quits Raven and shuts the background Claude down; "Hide window" in the menu keeps it running
with push-to-talk and no window."""

from __future__ import annotations

import math
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMenuBar,
                               QMessageBox, QPushButton, QScrollArea, QSystemTrayIcon, QToolButton, QVBoxLayout,
                               QWidget)

from . import macwin
from .core import Assistant, StartupError
from .status import Status

ASSET_DIR = Path(__file__).resolve().parent / "assets"

# Palette taken from the app icon: midnight navy surfaces, gold accent.
THEME = {"bg": "#0b1222", "surface": "#121a2e", "elevated": "#1a2440", "border": "#243050", "text": "#e8ecf4",
         "muted": "#8b95ab", "accent": "#e9c46a", "on_accent": "#1b1405", "error": "#ff6b6b",
         "frame": "#060b17", "rule": "#141f38"}  # frame: the slightly darker navy around the app; rule: hairline between sections
_COLORS = {"working": THEME["accent"], "waiting": "#b794f6", "done": "#5fd38d", "error": THEME["error"],
           "manual": "#6cb6ff", "idle": THEME["muted"], "listening": THEME["error"], "transcribing": "#6cb6ff"}


def _qss() -> str:
    t = THEME
    return f"""
    QToolTip {{ background: {t['elevated']}; color: {t['text']}; border: 1px solid {t['border']}; padding: 4px 8px; }}
    QFrame#topbar {{ background: {t['frame']}; border-bottom: 1px solid {t['rule']}; }}
    QFrame#composerband {{ background: {t['frame']}; border-top: 1px solid {t['rule']}; border-bottom: 1px solid {t['rule']}; }}
    QFrame#rail {{ background: {t['frame']}; }}
    QLabel#logo {{ border: 1px solid {t['border']}; border-radius: 9px; }}
    QLabel#title {{ color: {t['text']}; font-size: 17px; font-weight: 700; }}
    QFrame#pill {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 14px; }}
    QLabel#pillText {{ color: {t['text']}; font-size: 12px; font-weight: 600; }}
    QToolButton#persona {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 16px;
        padding: 6px 14px; color: {t['text']}; font-size: 13px; font-weight: 600; }}
    QToolButton#persona:hover {{ background: {t['elevated']}; }}
    QToolButton#persona::menu-indicator {{ image: none; width: 0px; }}
    QToolButton#more {{ border: none; border-radius: 8px; padding: 2px 8px; color: {t['muted']}; font-size: 20px; }}
    QToolButton#more:hover {{ background: {t['surface']}; color: {t['text']}; }}
    QToolButton#more::menu-indicator {{ image: none; width: 0px; }}
    QToolButton#folder {{ border: none; color: {t['muted']}; font-size: 12px; padding: 2px 4px; }}
    QToolButton#folder:hover {{ color: {t['text']}; }}
    QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 6px; color: {t['text']}; }}
    QMenu::item {{ padding: 7px 18px 7px 14px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {t['elevated']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 8px; }}
    QFrame#bubbleYou {{ background: {t['accent']}; border-radius: 17px; }}
    QLabel#youText {{ color: {t['on_accent']}; font-size: 14px; }}
    QFrame#bubbleRaven {{ background: {t['elevated']}; border-radius: 17px; }}
    QLabel#ravenText {{ color: {t['text']}; font-size: 14px; }}
    QLabel#caption {{ color: {t['muted']}; font-size: 11px; font-weight: 600; padding-left: 6px; }}
    QLabel#note {{ color: {t['muted']}; font-size: 12px; }}
    QLabel#hint {{ color: {t['muted']}; font-size: 14px; }}
    QLabel#footer {{ color: {t['muted']}; font-size: 11px; }}
    QFrame#errorBox {{ background: rgba(255,107,107,0.10); border: 1px solid rgba(255,107,107,0.35); border-radius: 12px; }}
    QLabel#errorText {{ color: #ff9d9d; font-size: 12px; }}
    QLineEdit#composer {{ background: {t['bg']}; border: 1px solid {t['border']}; border-radius: 23px;
        padding: 11px 18px; min-height: 22px; color: {t['text']}; font-size: 14px; selection-background-color: {t['accent']};
        selection-color: {t['on_accent']}; }}
    QLineEdit#composer:focus {{ border: 1px solid {t['accent']}; }}
    QLineEdit#composer:disabled {{ color: {t['muted']}; }}
    QPushButton#send {{ background: {t['accent']}; color: {t['on_accent']}; border: none; border-radius: 23px;
        font-size: 19px; font-weight: 800; min-width: 46px; max-width: 46px; min-height: 46px; max-height: 46px; }}
    QPushButton#send:hover {{ background: #f0d083; }}
    QPushButton#send:disabled {{ background: {t['elevated']}; color: {t['muted']}; }}
    QPushButton#terminal {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 12px;
        padding: 12px; color: {t['text']}; font-size: 13px; font-weight: 600; }}
    QPushButton#terminal:hover {{ background: {t['elevated']}; border-color: {t['accent']}; }}
    QPushButton#terminal:disabled {{ color: {t['muted']}; }}
    QPushButton#terminal[manual="true"] {{ background: {t['accent']}; color: {t['on_accent']}; border: none; }}
    QPushButton#terminal[manual="true"]:hover {{ background: #f0d083; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ width: 9px; background: transparent; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 4px; min-height: 36px; }}
    QScrollBar::handle:vertical:hover {{ background: #33426a; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


def apply_theme(app: QApplication) -> None:
    """Dark navy palette on the cross-platform Fusion style (the native macOS style ignores most of the
    palette), the system UI font, and the stylesheet above."""
    app.setStyle("Fusion")
    pal = QPalette()
    c = {k: QColor(v) for k, v in THEME.items()}
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "surface"), (QPalette.AlternateBase, "bg"),
                      (QPalette.Button, "surface"), (QPalette.ToolTipBase, "elevated"), (QPalette.Highlight, "accent")):
        pal.setColor(role, c[key])
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText, QPalette.BrightText):
        pal.setColor(role, c["text"])
    pal.setColor(QPalette.HighlightedText, c["on_accent"])
    pal.setColor(QPalette.PlaceholderText, c["muted"])
    pal.setColor(QPalette.Link, QColor(_COLORS["manual"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, c["muted"])
    app.setPalette(pal)
    font = QFont(".AppleSystemUIFont")
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(_qss())
    macwin.force_dark_appearance()  # dark title bars and standard dialogs, not just our own widgets


def parse_log(line: str) -> tuple[str, str]:
    """Assistant log line -> (speaker, text). 'you: hi' -> ('you', 'hi'); '[worker error] x' -> ('!', ...)."""
    if line.startswith("[") and "error" in line[:20]:  # real failures only; "[ack fallback]" etc. are dev noise
        return "!", line
    who, sep, rest = line.partition(": ")
    if sep and len(who) <= 24 and "[" not in who:
        return who, rest
    return "·", line


def app_pixmap(size: int) -> QPixmap:
    pm = QPixmap(str(ASSET_DIR / "icon.png"))
    if pm.isNull():  # packaging slip: still show something sensible instead of crashing
        pm = QPixmap(64, 64)
        pm.fill(QColor(THEME["surface"]))
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def logo_pixmap(size: int) -> QPixmap:
    """The small orbital logo (window header, menubar icon), rendered at 2x so it is sharp on Retina screens."""
    pm = QPixmap(str(ASSET_DIR / "logo.png"))
    if pm.isNull():
        return app_pixmap(size)
    pm = pm.scaled(size * 2, size * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pm.setDevicePixelRatio(2)
    return pm


def make_icon() -> QIcon:
    """The raven app icon (Dock when run unbundled, window icon, dialogs)."""
    return QIcon(app_pixmap(256))


def make_tray_icon() -> QIcon:
    return QIcon(logo_pixmap(64))


class Bridge(QObject):
    quit_req = Signal()
    line = Signal(str)
    ready = Signal()
    failed = Signal(str)
    notice = Signal(str)
    project_changed = Signal(str)


def _label(text: str, name: str, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.PlainText)  # never interpret model/Claude output as HTML
    return lab


class ChatView(QScrollArea):
    """The conversation as bubbles. You: gold, right. Raven: elevated navy, left, with the persona's name above.
    Consecutive lines from one speaker (a reply spoken in several chunks) join into one bubble."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self._lay = QVBoxLayout(body)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(6)
        # The empty-state hint floats centered over the (empty) viewport; messages stack up from the bottom,
        # newest next to the composer, like iMessage or Slack.
        self.hint = _label("Ask for something,\nout loud or in writing.", "hint")
        self.hint.setParent(self.viewport())
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hint.show()
        self._lay.addStretch(1)
        self.setWidget(body)
        self.entries: list[list[str]] = []     # [who, text]
        self._labels: list[QLabel] = []        # bubble text labels, for the width cap
        self._last: tuple[str, QLabel] | None = None

    def _insert(self, w: QWidget) -> None:
        self._lay.addWidget(w)

    def add(self, who: str, text: str) -> None:
        self.hint.hide()
        if self._last and self._last[0] == who and who not in ("!", "note"):
            self.entries[-1][1] += " " + text
            self._last[1].setText(self.entries[-1][1])
        else:
            self.entries.append([who, text])
            self._last = (who, self._make_row(who, text))
        self._apply_widths()
        QTimer.singleShot(40, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

    def _make_row(self, who: str, text: str) -> QLabel:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        if who in ("note", "!"):
            lab = _label(text, "note" if who == "note" else "errorText")
            lab.setAlignment(Qt.AlignCenter)
            if who == "!":
                frame = QFrame()
                frame.setObjectName("errorBox")
                fl = QVBoxLayout(frame)
                fl.setContentsMargins(12, 8, 12, 8)
                fl.addWidget(lab)
                h.addWidget(frame)
            else:
                h.addWidget(lab)
            self._insert(row)
            self._labels.append(lab)
            return lab
        mine = who == "you"
        lab = _label(text, "youText" if mine else "ravenText")
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble = QFrame()
        bubble.setObjectName("bubbleYou" if mine else "bubbleRaven")
        bl = QVBoxLayout(bubble)
        bl.setContentsMargins(15, 10, 15, 11)
        bl.addWidget(lab)
        if mine:
            h.addStretch(1)
            h.addWidget(bubble)
            self._insert(row)
        else:
            col = QWidget()
            v = QVBoxLayout(col)
            v.setContentsMargins(0, 8, 0, 0)
            v.setSpacing(4)
            v.addWidget(_label(who, "caption", wrap=False))
            h.addWidget(bubble)
            h.addStretch(1)
            v.addWidget(row)
            self._insert(col)
        self._labels.append(lab)
        return lab

    def _apply_widths(self) -> None:
        """Fix each label to the exact size its wrapped text needs (heightForWidth inside a scroll area
        under-reports and clips long messages)."""
        cap = max(160, int(self.viewport().width() * 0.78) - 32)
        for lab in self._labels:
            lab.ensurePolished()
            flags = int(Qt.TextWordWrap | Qt.AlignLeft)
            fm = lab.fontMetrics()
            width = min(cap, fm.boundingRect(0, 0, cap, 100_000, flags, lab.text()).width() + 10)  # slack: Qt's
            height = fm.boundingRect(0, 0, width, 100_000, flags, lab.text()).height() + 4         # real wrap can
            lab.setFixedSize(width, height)                                                        # differ slightly

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.hint.setGeometry(self.viewport().rect())
        self._apply_widths()

    def clear(self) -> None:
        """Back to the empty state (a new project starts a fresh conversation)."""
        for i in reversed(range(self._lay.count())):
            item = self._lay.itemAt(i)
            w = item.widget()
            if w is not None and w is not self.hint:
                self._lay.takeAt(i)
                w.setParent(None)  # gone from the window immediately, not whenever Qt gets to the deferred delete
                w.deleteLater()
        self.entries.clear()
        self._labels.clear()
        self._last = None
        self.hint.show()

    def plain_text(self) -> str:
        return "\n".join(f"{w}: {t}" for w, t in self.entries)


class RavenWindow(QWidget):
    hide_on_close = False

    def __init__(self, asst: Assistant, bridge: Bridge, project_dir: str | None = None):
        super().__init__()
        apply_theme(QApplication.instance())
        self.asst, self.bridge = asst, bridge
        self.project_dir = project_dir
        self._chrome_styled = False
        self._drag_from = None  # (mouse, Qt window pos, native origin) while following the mouse ourselves
        self.setWindowTitle("Raven")
        self.setWindowIcon(make_icon())
        self.resize(440, 740)
        self.setMinimumSize(380, 560)

        # header: logo, title, persona picker, menu
        logo = QLabel()
        logo.setObjectName("logo")
        logo.setPixmap(logo_pixmap(30))
        logo.setFixedSize(34, 34)
        logo.setAlignment(Qt.AlignCenter)
        title = _label("Raven", "title", wrap=False)
        self.persona = QToolButton()
        self.persona.setObjectName("persona")
        self.persona.setPopupMode(QToolButton.InstantPopup)
        self.persona.setCursor(Qt.PointingHandCursor)
        self.persona.setText("Dottie  ▾")
        self.persona_menu = QMenu(self.persona)
        self.persona.setMenu(self.persona_menu)
        more = QToolButton()
        more.setObjectName("more")
        more.setText("⋯")
        more.setPopupMode(QToolButton.InstantPopup)
        more.setCursor(Qt.PointingHandCursor)
        self.more_menu = QMenu(more)
        more.setMenu(self.more_menu)
        header = QHBoxLayout()
        header.setSpacing(10)
        for w in (logo, title):
            header.addWidget(w)
        header.addStretch(1)
        header.addWidget(self.persona)
        header.addWidget(more)

        # status capsule + project folder
        self.dot = QLabel()
        self.dot.setFixedSize(9, 9)
        self.status = _label("Loading models…", "pillText", wrap=False)
        pill = QFrame()
        pill.setObjectName("pill")
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(12, 6, 14, 6)
        pl.setSpacing(8)
        pl.addWidget(self.dot)
        pl.addWidget(self.status)
        self.folder = QToolButton()
        self.folder.setObjectName("folder")
        self.folder.setCursor(Qt.PointingHandCursor)
        self.folder.clicked.connect(self.open_project)
        self._set_folder_text()
        statusrow = QHBoxLayout()
        statusrow.addWidget(pill)
        statusrow.addStretch(1)
        statusrow.addWidget(self.folder)

        self.view = ChatView()

        # composer
        self.input = QLineEdit()
        self.input.setObjectName("composer")
        self.input.setPlaceholderText("Message Raven…")
        self.send = QPushButton("↑")
        self.send.setObjectName("send")
        self.send.setCursor(Qt.PointingHandCursor)
        composer = QHBoxLayout()
        composer.setSpacing(10)
        composer.addWidget(self.input, 1)
        composer.addWidget(self.send)

        self.terminal_btn = QPushButton("Open Claude session in Terminal")
        self.terminal_btn.setObjectName("terminal")
        self.terminal_btn.setCursor(Qt.PointingHandCursor)
        self.terminal_btn.setToolTip("Pauses Raven and opens this Claude Code session in a terminal so you can "
                                     "drive it by hand. Click again to hand it back.")
        footer = _label("Hold ⌥ Option to talk", "footer", wrap=False)
        footer.setAlignment(Qt.AlignCenter)

        # The window is a darker-navy frame around the content: a top bar, thin rails down both sides and along the
        # bottom, and a band behind the input row that splits the message window from the Claude button below.
        def rail(vertical: bool) -> QFrame:
            r = QFrame()
            r.setObjectName("rail")
            r.setFixedWidth(3) if vertical else r.setFixedHeight(3)
            return r

        self.topbar = QFrame()
        self.topbar.setObjectName("topbar")
        self._top_lay = QVBoxLayout(self.topbar)
        self._top_lay.setContentsMargins(17, 16, 17, 12)
        self._top_lay.setSpacing(12)
        self._top_lay.addLayout(header)
        self._top_lay.addLayout(statusrow)

        self.band = QFrame()
        self.band.setObjectName("composerband")
        band_lay = QVBoxLayout(self.band)
        band_lay.setContentsMargins(17, 12, 17, 12)
        band_lay.addLayout(composer)

        actions = QWidget()
        actions_lay = QVBoxLayout(actions)
        actions_lay.setContentsMargins(17, 12, 17, 12)
        actions_lay.setSpacing(10)
        actions_lay.addWidget(self.terminal_btn)
        actions_lay.addWidget(footer)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.topbar)
        column.addWidget(self.view, 1)
        column.addWidget(self.band)
        column.addWidget(actions)
        column.addWidget(rail(False))
        self._lay = self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._outer.addWidget(rail(True))
        self._outer.addLayout(column, 1)
        self._outer.addWidget(rail(True))

        self.input.returnPressed.connect(self._send)
        self.send.clicked.connect(self._send)
        self.terminal_btn.clicked.connect(self._toggle_manual)
        bridge.line.connect(self.on_line)
        bridge.ready.connect(self._on_ready)
        bridge.notice.connect(lambda m: self.view.add("!", m))
        bridge.project_changed.connect(self._on_project_changed)
        self._build_menus()
        self._set_enabled(False)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(150)

    # ── conversation ──
    def on_line(self, line: str) -> None:
        who, text = parse_log(line)
        if who == "·":  # developer log noise stays out of the chat; only persona changes are worth showing
            if text.startswith("persona -> "):
                self.view.add("note", f"Switched to {text[len('persona -> '):]}")
            return
        self.view.add(who, text)

    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            threading.Thread(target=self.asst.handle_text, args=(text,), daemon=True).start()

    # ── persona ──
    def _fill_personas(self) -> None:
        self.persona_menu.clear()
        active = self.asst.persona.id
        for r in self.asst.list_personas():
            act = QAction(r["name"], self.persona_menu)
            act.setCheckable(True)
            act.setChecked(r["id"] == active)
            act.triggered.connect(lambda _=False, n=r["name"]: self._pick_persona(n))
            self.persona_menu.addAction(act)
        self.persona.setText(f"{self.asst.persona.name}  ▾")

    def persona_names(self) -> list[str]:
        return [a.text() for a in self.persona_menu.actions()]

    def _pick_persona(self, name: str) -> None:
        def run():
            try:
                self.asst.set_persona(name)
            except Exception as e:
                self.bridge.notice.emit(str(e))
        threading.Thread(target=run, daemon=True).start()

    # ── projects (File menu) ──
    def _set_folder_text(self) -> None:
        d = self.project_dir
        self.folder.setText(f"{Path(d).name or d}  ›" if d else "")
        self.folder.setToolTip(f"Claude works in {d}\nClick to open another project" if d else "")
        self.folder.setVisible(bool(d))

    def _build_menus(self) -> None:
        """File > New Project… / Open Project… in the macOS menu bar (a parentless QMenuBar becomes the global
        one), and the same two items in the ⋯ menu for people who don't look up there."""
        self.menubar = QMenuBar()
        file_menu = self.menubar.addMenu("File")
        self.act_new = file_menu.addAction("New Project…", self.new_project)
        self.act_new.setShortcut(QKeySequence.New)
        self.act_open = file_menu.addAction("Open Project…", self.open_project)
        self.act_open.setShortcut(QKeySequence.Open)
        if sys.platform != "darwin":
            self._lay.setMenuBar(self.menubar)
        m = self.more_menu
        m.addAction("New Project…", self.new_project)
        m.addAction("Open Project…", self.open_project)
        m.addSeparator()
        m.addAction("Hide window (keep running)", self.hide)
        m.addAction("Quit Raven", QApplication.instance().quit)

    def open_project(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Open Project", self.project_dir or str(Path.home()))
        if d:
            self._switch_project(d)

    def new_project(self) -> None:
        """Pick where to put a new folder and what to call it, create it, and open it."""
        start = str(Path(self.project_dir or Path.home()) / "New Project")
        path, _ = QFileDialog.getSaveFileName(self, "New Project", start)
        if not path:
            return
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.bridge.notice.emit(f"Couldn't create that folder: {e}")
            return
        self._switch_project(path)

    def _switch_project(self, path: str) -> None:
        def run():
            try:
                self.bridge.project_changed.emit(self.asst.open_project(path))
            except Exception as e:
                self.bridge.notice.emit(str(e))
        threading.Thread(target=run, daemon=True).start()

    def _on_project_changed(self, path: str) -> None:
        self.project_dir = path
        self._set_folder_text()
        self.view.clear()
        self.view.add("note", f"Opened {Path(path).name or path}")

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

    def _set_manual_style(self, manual: bool) -> None:
        if self.terminal_btn.property("manual") != manual:
            self.terminal_btn.setProperty("manual", manual)
            self.terminal_btn.style().unpolish(self.terminal_btn)
            self.terminal_btn.style().polish(self.terminal_btn)

    def refresh(self) -> None:
        st = self.asst.status
        if st is None:
            return
        text = re.sub(r"^\W+", "", st.text()[1].removeprefix("Raven: "))  # drop the terminal spinner/check glyph
        text = text[:1].upper() + text[1:]
        key = st.overlay or st.state
        # Shorter wording than the terminal status line: this capsule has to fit beside the folder name.
        text = {"idle": "Ready", "manual": "Claude open in Terminal", "waiting": "Waiting for your answer",
                "listening": "Listening…", "transcribing": "Transcribing…"}.get(key, text)
        if key == "error":
            text = "Claude hit an error"
        self.status.setText(text)
        color = QColor(_COLORS.get(key, _COLORS["idle"]))
        if key in ("working", "listening", "transcribing"):  # a gentle pulse while something is happening
            color.setAlphaF(0.55 + 0.45 * (0.5 + 0.5 * math.sin(time.monotonic() * 5)))
        self.dot.setStyleSheet(f"background: rgba({color.red()},{color.green()},{color.blue()},{color.alphaF():.2f});"
                               "border-radius: 4px;")
        manual = self.asst.manual
        self.terminal_btn.setText("Hand back to Raven" if manual else "Open Claude session in Terminal")
        self._set_manual_style(manual)
        if self.persona_menu.actions():
            self.terminal_btn.setEnabled(True)
            self.input.setEnabled(not manual)
            self.send.setEnabled(not manual)
            active = self.asst.persona
            for a in self.persona_menu.actions():
                a.setChecked(a.text() == active.name)
            label = f"{active.name}  ▾"
            if self.persona.text() != label:
                self.persona.setText(label)

    def _drag_zone_bottom(self) -> int:
        """Bottom edge of the strip you can grab to move the window: the whole top bar (traffic lights, header and
        status row). Buttons and the persona pill inside it consume their own clicks."""
        return self.topbar.geometry().bottom()

    def mousePressEvent(self, e) -> None:
        """With the native title bar hidden, Qt's content covers the drag strip, so moving the window is ours to
        do. Ask the platform to start the move; if it declines, follow the mouse ourselves. Buttons, the persona
        pill and the menus consume their own clicks; only empty header space reaches here."""
        if e.button() == Qt.LeftButton and e.position().y() <= self._drag_zone_bottom():
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                e.accept()
                return
            self._drag_from = (e.globalPosition().toPoint(), self.pos(), macwin.window_origin(self))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None and e.buttons() & Qt.LeftButton:
            start, origin, native = self._drag_from
            d = e.globalPosition().toPoint() - start
            # On macOS move the native window itself (Qt's move() is off by the old title-bar height); the screen's
            # y axis points up there, hence "minus". Elsewhere use Qt's own move().
            if not (native and macwin.set_window_origin(self, native[0] + d.x(), native[1] - d.y())):
                self.move(origin + d)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._drag_from = None
        super().mouseReleaseEvent(e)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._chrome_styled:
            self._chrome_styled = True
            QTimer.singleShot(0, self._style_chrome)

    def _style_chrome(self) -> None:
        if macwin.unify_titlebar(self, THEME["frame"]):
            m = self._top_lay.contentsMargins()  # the content now runs under the title bar: clear the traffic lights
            self._top_lay.setContentsMargins(m.left(), m.top() + 26, m.right(), m.bottom())

    def closeEvent(self, e) -> None:
        """Closing the window quits Raven, as people expect. (Hiding instead left an invisible process running
        in the launching terminal.) Use the menu's "Hide window" to keep the hotkey without a window."""
        if self.hide_on_close:
            e.ignore()
            self.hide()
            return
        e.accept()
        QApplication.instance().quit()


def shutdown(asst: Assistant) -> None:
    """Stop speaking and shut the background Claude down so no orphan `claude` process is left behind."""
    try:
        asst.barge_in()
    except Exception:
        pass
    try:
        asst.svc.worker.close()
    except Exception:
        pass


def _selftest(asst: Assistant, bridge: Bridge, text: str) -> None:
    """RAVEN_SELFTEST="message": exercise the whole worker path from a packaged app and log what happened."""
    import logging

    lg = logging.getLogger("raven")
    done = threading.Event()
    orig = asst._on_worker_event

    def tap(ev):
        lg.info("selftest event: %s %r", ev.kind, ev.text[:120])
        orig(ev)
        if ev.kind in ("done", "error"):
            done.set()

    asst.svc.worker.on_event = tap
    lg.info("selftest: sending %r", text)
    asst.handle_text(text)
    lg.info("selftest: finished=%s", done.wait(150))
    asst.wait()
    lg.info("selftest: complete")
    bridge.quit_req.emit()


def run_ui(asst: Assistant, enable_ptt: bool = True, project_dir: str | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Raven")
    app.setWindowIcon(make_icon())
    bridge = Bridge()
    asst.log = bridge.line.emit
    asst.status = asst.status or Status(enabled=False)  # state only; the window draws it
    asst.project_dir = project_dir
    win = RavenWindow(asst, bridge, project_dir)

    tray = QSystemTrayIcon(make_tray_icon(), app)
    menu = QMenu()
    show = QAction("Show Raven")
    show.triggered.connect(lambda: (win.show(), win.raise_(), win.activateWindow()))
    hide = QAction("Hide window (keep running)")
    hide.triggered.connect(win.hide)
    term = QAction("Open Claude session in Terminal / Hand back")
    term.triggered.connect(win._toggle_manual)
    quit_ = QAction("Quit Raven")
    quit_.triggered.connect(app.quit)
    for a in (show, hide, term, quit_):
        menu.addAction(a)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: show.trigger() if reason == QSystemTrayIcon.Trigger else None)
    tray.show()
    win.show()

    app.aboutToQuit.connect(lambda: shutdown(asst))
    signal.signal(signal.SIGINT, lambda *_: app.quit())  # Ctrl+C in the launching terminal quits cleanly
    pulse = QTimer()                                     # lets Python run the signal handler while Qt is idle
    pulse.start(250)
    pulse.timeout.connect(lambda: None)

    def boot():
        import logging

        lg = logging.getLogger("raven")
        try:
            lg.info("loading voice and models")
            asst.prepare()   # loads the voice, downloading its pack first if needed
            asst.startup()
        except Exception as e:  # StartupError, a failed pack download, a missing file... all end in one dialog
            lg.exception("startup failed")
            bridge.failed.emit(str(e) or type(e).__name__)
            return
        lg.info("ready")
        bridge.ready.emit()
        if os.environ.get("RAVEN_SELFTEST"):  # diagnostic: send one message, log the outcome, quit
            threading.Thread(target=_selftest, args=(asst, bridge, os.environ["RAVEN_SELFTEST"]), daemon=True).start()
            return
        if enable_ptt and not os.environ.get("RAVEN_NO_PTT"):
            from . import ptt

            ptt.run(asst)  # global hotkey; works with the window closed

    def fail(msg: str) -> None:
        QMessageBox.critical(win, "Raven couldn't start", msg)
        app.exit(1)

    bridge.failed.connect(fail)
    bridge.quit_req.connect(app.quit)
    threading.Thread(target=boot, daemon=True, name="boot").start()
    return app.exec()
