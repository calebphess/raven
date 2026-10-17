import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from raven2 import terminal as T
from raven2.status import Status
from raven2.ui import Bridge, RavenWindow, parse_log, render_entries
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
    win = RavenWindow(a, bridge)
    bridge.ready.emit()
    qapp.processEvents()
    opened = []
    monkeypatch.setattr(T, "open_in_terminal", lambda cmd, app="Terminal": opened.append(cmd))
    monkeypatch.setattr(T.shutil, "which", lambda n: "/bin/claude")
    yield qapp, win, a, w, opened, bridge
    win.timer.stop()


def test_log_parsing_and_merging():
    assert parse_log("you: hello there") == ("you", "hello there")
    assert parse_log("Dottie: Systems online.") == ("Dottie", "Systems online.")
    assert parse_log("[worker error] boom")[0] == "!"
    assert parse_log("loading models...")[0] == "·"
    html = render_entries([("Dottie", "Part one,"), ("Dottie", "part two."), ("you", "<b>hi</b>")])
    assert html.count("Dottie") == 1 and "Part one, part two." in html   # chunks read as one message
    assert "&lt;b&gt;hi&lt;/b&gt;" in html                                 # user text is escaped


def test_window_enables_after_ready_and_lists_personas(ui):
    qapp, win, a, w, opened, bridge = ui
    assert win.input.isEnabled() and win.terminal_btn.isEnabled()
    names = [win.persona.itemText(i) for i in range(win.persona.count())]
    assert {"Dottie", "Charles", "Raven"} <= set(names) and win.persona.currentText() == "Dottie"


def test_conversation_and_status_show_in_the_window(ui):
    qapp, win, a, w, opened, bridge = ui
    bridge.line.emit("you: check the tests please now")
    bridge.line.emit("Dottie: Running the tests.")
    qapp.processEvents()
    assert "check the tests" in win.view.toPlainText() and "Running the tests." in win.view.toPlainText()
    a.status.working(True); a.status.tool()
    win.refresh()
    assert "Claude working" in win.status.text() and "1 tool" in win.status.text()


def test_typing_a_message_sends_it_to_claude(ui):
    qapp, win, a, w, opened, bridge = ui
    win.input.setText("explain what this project does")
    win.send.click()
    assert pump(qapp, lambda: w.sent == ["explain what this project does"]) and win.input.text() == ""


def test_terminal_button_takes_over_and_hands_back(ui):
    qapp, win, a, w, opened, bridge = ui
    assert win.terminal_btn.text() == "Open Claude session in Terminal"
    win.terminal_btn.click()
    assert pump(qapp, lambda: a.manual)
    win.refresh()
    assert opened == ["cd /work/proj && env -u RAVEN /bin/claude --resume sess-1"]
    assert win.terminal_btn.text() == "Hand back to Raven" and "terminal" in win.status.text()
    assert not win.input.isEnabled()                       # nothing can be sent while the terminal owns it
    win.terminal_btn.setEnabled(True)
    win.terminal_btn.click()
    assert pump(qapp, lambda: not a.manual)
    win.refresh()
    assert win.terminal_btn.text() == "Open Claude session in Terminal" and win.input.isEnabled()


def test_terminal_button_with_no_session_explains_itself(ui):
    qapp, win, a, w, opened, bridge = ui
    w.session = None
    win.terminal_btn.click()
    assert pump(qapp, lambda: "no claude session yet" in win.view.toPlainText().lower())
    win.refresh()
    assert not a.manual and opened == [] and win.terminal_btn.isEnabled()


def test_persona_picker_switches_the_live_persona(ui):
    qapp, win, a, w, opened, bridge = ui
    win.persona.setCurrentIndex(win.persona.findData("charles"))
    win._pick_persona(win.persona.currentIndex())
    assert pump(qapp, lambda: a.persona.id == "charles")


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
