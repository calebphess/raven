import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QLabel

from raven2 import terminal as T
from raven2.status import Status
from raven2.ui import THEME, Bridge, ChatView, RavenWindow, _COLORS, parse_log
from test_v2 import make_with_worker


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def pump(qapp, cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    qapp.processEvents()
    return cond()


@pytest.fixture
def ui(qapp, monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    a.status = Status(enabled=False)
    bridge = Bridge()
    a.log = bridge.line.emit
    win = RavenWindow(a, bridge, project_dir="/work/proj")
    bridge.ready.emit()
    qapp.processEvents()
    opened = []
    monkeypatch.setattr(T, "open_in_terminal", lambda cmd, app="Terminal": opened.append(cmd))
    monkeypatch.setattr(T.shutil, "which", lambda n: "/bin/claude")
    yield qapp, win, a, w, opened, bridge
    win.timer.stop()


def bubbles(win):
    return [f for f in win.view.findChildren(QFrame) if f.objectName() in ("bubbleYou", "bubbleRaven")]


# ── log -> chat ──
def test_log_parsing():
    assert parse_log("you: hello there") == ("you", "hello there")
    assert parse_log("Dottie: Systems online.") == ("Dottie", "Systems online.")
    assert parse_log("[worker error] boom")[0] == "!"
    assert parse_log("loading models...")[0] == "·"


def test_your_messages_and_ravens_are_different_bubbles(ui):
    qapp, win, a, w, opened, bridge = ui
    for line in ("you: what time is it", "Dottie: Checking the clock."):
        bridge.line.emit(line)
    qapp.processEvents()
    kinds = [b.objectName() for b in bubbles(win)]
    assert kinds == ["bubbleYou", "bubbleRaven"]
    assert win.view.hint.isHidden()                       # the empty-state hint goes away after the first message


def test_chunks_of_one_reply_join_into_one_bubble(ui):
    qapp, win, a, w, opened, bridge = ui
    for line in ("Dottie: Part one.", "Dottie: Part two.", "you: ok", "Dottie: Another reply."):
        bridge.line.emit(line)
    qapp.processEvents()
    assert win.view.entries == [["Dottie", "Part one. Part two."], ["you", "ok"], ["Dottie", "Another reply."]]
    assert len(bubbles(win)) == 3


def test_developer_log_noise_stays_out_of_the_chat(ui):
    qapp, win, a, w, opened, bridge = ui
    for line in ("loading models...", "[ack fallback] model took over 2s", "something odd happened"):
        bridge.line.emit(line)
    bridge.line.emit("persona -> Charles")
    qapp.processEvents()
    texts = [t for _, t in win.view.entries]
    assert texts == ["Switched to Charles"]               # only the useful event is shown, as a quiet note
    assert win.view.hint.isVisible() is False


def test_errors_get_their_own_styled_box(ui):
    qapp, win, a, w, opened, bridge = ui
    bridge.line.emit("[worker error] Claude couldn't start")
    qapp.processEvents()
    assert any(f.objectName() == "errorBox" for f in win.view.findChildren(QFrame))


def test_bubble_text_is_never_html(ui):
    qapp, win, a, w, opened, bridge = ui
    bridge.line.emit("Dottie: <b>bold</b> & <script>x</script>")
    qapp.processEvents()
    lab = [l for l in win.view.findChildren(QLabel) if l.objectName() == "ravenText"][0]
    assert lab.textFormat() == Qt.PlainText and "<b>bold</b>" in lab.text()


def test_long_messages_are_never_clipped(ui):
    """Regression: wrapped labels inside the scroll area under-reported their height and cut text off."""
    qapp, win, a, w, opened, bridge = ui
    win.show()
    long = ("The status module pins a one-line status bar to the bottom of the terminal. It shows idle, working, "
            "waiting for you, done, or error. Want more detail on any part of it, or should I move on?")
    bridge.line.emit("Dottie: " + long)
    bridge.line.emit("you: " + long)
    bridge.line.emit("[worker error] " + long)
    qapp.processEvents()
    for w_ in (400, 520):
        win.resize(w_, 700)
        qapp.processEvents()
        for lab in win.view.findChildren(QLabel):
            if lab.objectName() in ("ravenText", "youText", "errorText"):
                need = lab.fontMetrics().boundingRect(0, 0, lab.width(), 100_000,
                                                      int(Qt.TextWordWrap | Qt.AlignLeft), lab.text()).height()
                assert lab.height() >= need, (lab.objectName(), w_, lab.height(), need)
                assert lab.width() <= win.view.viewport().width()


def test_bubbles_cap_their_width_for_readability(ui):
    qapp, win, a, w, opened, bridge = ui
    win.show(); win.resize(700, 700)
    bridge.line.emit("Dottie: " + "word " * 120)
    qapp.processEvents()
    lab = [l for l in win.view.findChildren(QLabel) if l.objectName() == "ravenText"][0]
    assert lab.width() <= int(win.view.viewport().width() * 0.78)


# ── window state ──
def test_window_enables_after_ready_and_lists_personas(ui):
    qapp, win, a, w, opened, bridge = ui
    assert win.input.isEnabled() and win.terminal_btn.isEnabled() and win.persona.isEnabled()
    assert {"Dottie", "Charles", "Raven"} <= set(win.persona_names())
    assert "Dottie" in win.persona.text()
    checked = [x.text() for x in win.persona_menu.actions() if x.isChecked()]
    assert checked == ["Dottie"]


def test_status_capsule_reflects_claude(ui):
    qapp, win, a, w, opened, bridge = ui
    win.refresh()
    assert win.status.text() == "Ready"
    a.status.working(True); a.status.tool(); a.status.tool()
    win.refresh()
    assert win.status.text() == "Claude working 0s · 2 tools"          # no terminal spinner glyph in the UI
    a.status.waiting("yes, always, or no?"); win.refresh()
    assert win.status.text() == "Waiting for your answer"
    a.status.done(); win.refresh()
    assert win.status.text().startswith("Claude finished")
    a.status.error("boom"); win.refresh()
    assert win.status.text() == "Claude hit an error"
    a.status.listen("listening"); win.refresh()
    assert win.status.text() == "Listening…"


def test_status_labels_fit_the_capsule(ui):
    qapp, win, a, w, opened, bridge = ui
    win.show(); qapp.processEvents()
    a.status.manual(); win.refresh(); qapp.processEvents()
    assert win.status.text() == "Claude open in Terminal"
    assert win.status.fontMetrics().horizontalAdvance(win.status.text()) < 220   # fits beside the folder name


def test_project_folder_is_shown_and_optional(ui, qapp):
    _, win, a, w, opened, bridge = ui
    assert win.folder.text().startswith("proj") and "/work/proj" in win.folder.toolTip()
    win2 = RavenWindow(a, Bridge())
    assert win2.folder.isHidden()
    win2.timer.stop()


def test_typing_a_message_sends_it_to_claude(ui):
    qapp, win, a, w, opened, bridge = ui
    win.input.setText("explain what this project does")
    win.send.click()
    assert pump(qapp, lambda: w.sent == ["explain what this project does"]) and win.input.text() == ""


def test_terminal_button_takes_over_and_hands_back(ui):
    qapp, win, a, w, opened, bridge = ui
    assert win.terminal_btn.text() == "Open Claude session in Terminal" and not win.terminal_btn.property("manual")
    win.terminal_btn.click()
    assert pump(qapp, lambda: a.manual)
    win.refresh()
    assert opened == ["cd /work/proj && env -u RAVEN /bin/claude --resume sess-1"]
    assert win.terminal_btn.text() == "Hand back to Raven" and win.terminal_btn.property("manual") is True
    assert win.status.text() == "Claude open in Terminal"
    assert not win.input.isEnabled() and not win.send.isEnabled()      # nothing can be sent while the terminal owns it
    win.terminal_btn.setEnabled(True)
    win.terminal_btn.click()
    assert pump(qapp, lambda: not a.manual)
    win.refresh()
    assert win.terminal_btn.text() == "Open Claude session in Terminal" and win.input.isEnabled()
    assert not win.terminal_btn.property("manual")


def test_terminal_button_with_no_session_explains_itself(ui):
    qapp, win, a, w, opened, bridge = ui
    w.session = None
    win.terminal_btn.click()
    assert pump(qapp, lambda: any("no claude session yet" in t.lower() for _, t in win.view.entries))
    win.refresh()
    assert not a.manual and opened == [] and win.terminal_btn.isEnabled()


def test_persona_menu_switches_the_live_persona(ui):
    qapp, win, a, w, opened, bridge = ui
    next(x for x in win.persona_menu.actions() if x.text() == "Charles").trigger()
    assert pump(qapp, lambda: a.persona.id == "charles")
    win.refresh()
    assert "Charles" in win.persona.text()
    assert [x.text() for x in win.persona_menu.actions() if x.isChecked()] == ["Charles"]


def test_change_folder_menu_item_only_when_supported(ui, qapp):
    _, win, a, w, opened, bridge = ui
    assert "Change project folder…" not in [x.text() for x in win.more_menu.actions()]
    calls = []
    win2 = RavenWindow(a, Bridge(), "/work/proj", on_change_folder=lambda wnd: calls.append(wnd))
    assert "Change project folder…" in [x.text() for x in win2.more_menu.actions()]
    win2.folder.click()
    assert calls == [win2]
    win2.timer.stop()


def test_closing_the_window_quits_the_app(ui, monkeypatch):
    qapp, win, a, w, opened, bridge = ui
    quits = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))
    win.show()
    win.close()
    assert quits == [1]


def test_hide_on_close_is_opt_in(ui, monkeypatch):
    qapp, win, a, w, opened, bridge = ui
    quits = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(1)))
    win.hide_on_close = True
    win.show(); win.close()
    assert quits == [] and not win.isVisible()


def test_shutdown_stops_the_background_claude_and_survives_errors(ui):
    from raven2.ui import shutdown
    qapp, win, a, w, opened, bridge = ui
    closed = []
    w.close = lambda: closed.append(1)
    shutdown(a)
    assert closed == [1]
    w.close = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    shutdown(a)                                  # an error while shutting down must not block quitting


# ── look ──
def test_window_uses_the_dark_navy_theme(ui):
    qapp, win, a, w, opened, bridge = ui
    from PySide6.QtGui import QPalette
    pal = qapp.palette()
    lum = lambda c: 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    assert lum(pal.color(QPalette.Window)) < 40 and lum(pal.color(QPalette.Base)) < 50
    assert lum(pal.color(QPalette.WindowText)) > 180
    assert pal.color(QPalette.Window).blue() > pal.color(QPalette.Window).red()      # navy, not neutral grey


def test_status_colors_are_readable_on_the_dark_window():
    def lum(h): return 0.299 * int(h[1:3], 16) + 0.587 * int(h[3:5], 16) + 0.114 * int(h[5:7], 16)
    assert all(lum(c) > 120 for c in _COLORS.values())
    assert THEME["accent"] == "#e9c46a"                                              # the icon's gold


def test_composer_is_tall_enough_to_keep_its_pill_corners(ui):
    """Regression: Qt silently drops border-radius when the box is shorter than twice the radius."""
    qapp, win, a, w, opened, bridge = ui
    win.show(); qapp.processEvents()
    assert win.input.height() >= 2 * 23 and win.send.width() == win.send.height() == 46


def test_app_logo_asset_ships_with_the_package():
    from raven2.ui import ASSET_DIR, app_pixmap
    assert (ASSET_DIR / "icon.png").exists() and not app_pixmap(30).isNull()
