import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QFrame, QLabel

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


def test_file_menu_has_new_and_open_project(ui):
    qapp, win, a, w, opened, bridge = ui
    menus = {m.title(): m for m in [act.menu() for act in win.menubar.actions()]}
    assert list(menus) == ["File"]
    items = [x.text() for x in menus["File"].actions()]
    assert items == ["New Project…", "Open Project…"]
    from PySide6.QtGui import QKeySequence
    assert win.act_new.shortcut() == QKeySequence(QKeySequence.New) and win.act_open.shortcut() == QKeySequence(QKeySequence.Open)
    assert {"New Project…", "Open Project…"} <= {x.text() for x in win.more_menu.actions()}   # also in the ⋯ menu


def test_open_project_switches_in_place_and_starts_a_fresh_chat(ui, monkeypatch, tmp_path):
    qapp, win, a, w, opened, bridge = ui
    proj = tmp_path / "my-app"; proj.mkdir()
    switched = []
    w.open_project = lambda cwd: switched.append(cwd)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *x, **k: str(proj)))
    bridge.line.emit("you: hello from the old project"); qapp.processEvents()
    assert len(win.view.entries) == 1
    win.act_open.trigger()
    assert pump(qapp, lambda: win.project_dir == str(proj))
    assert switched == [str(proj)] and a.project_dir == str(proj)
    assert win.view.entries == [["note", "Opened my-app"]]                    # the old conversation is gone
    assert win.folder.text().startswith("my-app") and str(proj) in win.folder.toolTip()
    assert win.view.hint.isHidden()


def test_open_project_cancel_changes_nothing(ui, monkeypatch):
    qapp, win, a, w, opened, bridge = ui
    w.open_project = lambda cwd: (_ for _ in ()).throw(AssertionError("must not switch"))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *x, **k: ""))
    win.act_open.trigger(); qapp.processEvents()
    assert win.project_dir == "/work/proj"


def test_new_project_creates_the_folder_and_opens_it(ui, monkeypatch, tmp_path):
    qapp, win, a, w, opened, bridge = ui
    target = tmp_path / "brand new" / "cool-idea"                              # nested: parents get created too
    switched = []
    w.open_project = lambda cwd: switched.append(cwd)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *x, **k: (str(target), "")))
    win.act_new.trigger()
    assert pump(qapp, lambda: win.project_dir == str(target))
    assert target.is_dir() and switched == [str(target)]
    assert win.view.entries == [["note", "Opened cool-idea"]]


def test_new_project_cancel_creates_nothing(ui, monkeypatch, tmp_path):
    qapp, win, a, w, opened, bridge = ui
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *x, **k: ("", "")))
    win.act_new.trigger(); qapp.processEvents()
    assert win.project_dir == "/work/proj" and list(tmp_path.iterdir()) == []


def test_switching_projects_is_refused_while_a_terminal_owns_the_session(ui, monkeypatch, tmp_path):
    qapp, win, a, w, opened, bridge = ui
    w.detached = True
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *x, **k: str(tmp_path)))
    win.act_open.trigger()
    assert pump(qapp, lambda: any("Hand Claude back" in t for _, t in win.view.entries))
    assert win.project_dir == "/work/proj"


def test_clicking_the_folder_chip_opens_the_project_picker(ui, monkeypatch):
    qapp, win, a, w, opened, bridge = ui
    asked = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *x, **k: asked.append(1) or ""))
    win.folder.click()
    assert asked == [1]


def test_title_bar_styling_is_a_safe_noop_off_the_cocoa_platform(ui):
    """The headless test platform has no native macOS window: styling must quietly do nothing, not raise."""
    from raven2 import macwin
    qapp, win, a, w, opened, bridge = ui
    assert qapp.platformName() == "offscreen"
    assert macwin.force_dark_appearance() is False and macwin.unify_titlebar(win, THEME["bg"]) is False
    win.show(); qapp.processEvents(); qapp.processEvents()                       # showEvent -> _style_chrome path
    assert win._chrome_styled and win._top_lay.contentsMargins().top() == 16      # no extra room reserved when unstyled


def test_chat_clear_returns_to_the_empty_state(ui):
    qapp, win, a, w, opened, bridge = ui
    for line in ("you: hi", "Dottie: hello", "[worker error] boom"):
        bridge.line.emit(line)
    qapp.processEvents()
    win.view.clear()
    assert win.view.entries == [] and win.view.hint.isVisibleTo(win.view) and bubbles(win) == []


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


def test_both_icon_assets_ship_with_the_package_and_are_different_images():
    from raven2.ui import ASSET_DIR, app_pixmap, logo_pixmap, make_icon, make_tray_icon
    for name in ("icon.png", "logo.png"):
        assert (ASSET_DIR / name).exists(), name
    assert not app_pixmap(30).isNull() and not logo_pixmap(30).isNull()
    assert (ASSET_DIR / "icon.png").read_bytes() != (ASSET_DIR / "logo.png").read_bytes()    # raven vs orbital circle
    assert not make_icon().isNull() and not make_tray_icon().isNull()


def test_header_shows_the_small_logo_at_retina_resolution(ui):
    from PySide6.QtCore import QSize
    from raven2.ui import logo_pixmap
    qapp, win, a, w, opened, bridge = ui
    src = logo_pixmap(30)
    assert src.devicePixelRatio() == 2 and src.width() == 60 and src.deviceIndependentSize() == QSize(30, 30)
    lab = next(l for l in win.findChildren(QLabel) if l.objectName() == "logo")
    assert not lab.pixmap().isNull() and lab.pixmap().deviceIndependentSize() == QSize(30, 30)


def test_icons_are_clean_cutouts_with_no_opaque_corners_or_holes():
    from PySide6.QtGui import QImage
    from raven2.ui import ASSET_DIR
    for name in ("icon.png", "logo.png"):
        img = QImage(str(ASSET_DIR / name)).convertToFormat(QImage.Format_ARGB32)
        w = img.width()
        assert all(img.pixelColor(x, y).alpha() == 0 for x, y in ((1, 1), (w - 2, 1), (1, w - 2), (w - 2, w - 2))), name
        assert img.pixelColor(w // 2, w // 2).alpha() == 255, name
        holes = sum(1 for y in range(40, w - 40, 5) for x in range(40, w - 40, 5) if img.pixelColor(x, y).alpha() < 250)
        assert holes < 40, (name, holes)   # inside the shape, nothing see-through (the old art had transparent holes)


# ── the darker-navy frame ──
def lum(hex_):
    return 0.299 * int(hex_[1:3], 16) + 0.587 * int(hex_[3:5], 16) + 0.114 * int(hex_[5:7], 16)


def test_frame_is_a_slightly_darker_navy_than_the_content():
    assert lum(THEME["frame"]) < lum(THEME["bg"])                                   # darker...
    assert lum(THEME["bg"]) - lum(THEME["frame"]) < 12                              # ...but only slightly
    f = THEME["frame"]
    assert int(f[5:7], 16) > int(f[1:3], 16) * 2                                    # still navy (blue well above red), not grey/black


def test_top_bar_wraps_the_header_and_status_and_is_the_whole_drag_strip(ui):
    qapp, win, a, w, opened, bridge = ui
    win.show(); qapp.processEvents()
    assert win.topbar.objectName() == "topbar"
    for child in (win.persona, win.status, win.folder):
        assert win.topbar.isAncestorOf(child)
    assert win._drag_zone_bottom() == win.topbar.geometry().bottom()
    assert win.topbar.geometry().top() == 0 and win.topbar.width() == win.width() - 6   # spans between the two rails


def test_thin_rails_run_down_both_sides_and_along_the_bottom(ui):
    qapp, win, a, w, opened, bridge = ui
    win.show(); qapp.processEvents()
    rails = [f for f in win.findChildren(QFrame) if f.objectName() == "rail"]
    sides = [r for r in rails if r.width() == 3 and r.height() > 100]
    bottom = [r for r in rails if r.height() == 3 and r.width() > 100]
    assert len(sides) == 2 and len(bottom) == 1
    assert sorted(r.geometry().left() for r in sides) == [0, win.width() - 3]       # hugging the left and right edges
    assert all(r.height() == win.height() for r in sides)                           # full height, beside the top bar too
    assert bottom[0].geometry().bottom() == win.height() - 1


def test_a_band_splits_the_message_window_from_the_claude_button(ui):
    """Message window / input band / Claude button, top to bottom."""
    qapp, win, a, w, opened, bridge = ui
    win.show(); qapp.processEvents()
    assert win.band.objectName() == "composerband"
    assert win.band.isAncestorOf(win.input) and win.band.isAncestorOf(win.send)
    assert not win.band.isAncestorOf(win.terminal_btn)
    top = lambda wdg: wdg.mapTo(win, wdg.rect().topLeft()).y()
    bottom = lambda wdg: top(wdg) + wdg.height()
    assert top(win.topbar) < bottom(win.topbar) <= top(win.view) < bottom(win.view) <= top(win.band)
    assert bottom(win.band) <= top(win.terminal_btn)


def test_native_window_color_matches_the_frame_so_edges_blend(ui, monkeypatch):
    from raven2 import macwin
    qapp, win, a, w, opened, bridge = ui
    seen = []
    monkeypatch.setattr(macwin, "unify_titlebar", lambda w_, color: seen.append(color) or False)
    win._chrome_styled = False
    win._style_chrome()
    assert seen == [THEME["frame"]]


def test_traffic_light_room_is_added_inside_the_top_bar(ui, monkeypatch):
    from raven2 import macwin
    qapp, win, a, w, opened, bridge = ui
    before = win._top_lay.contentsMargins().top()
    monkeypatch.setattr(macwin, "unify_titlebar", lambda w_, color: True)          # pretend we're on a real Cocoa window
    win._style_chrome()
    assert win._top_lay.contentsMargins().top() == before + 26
    assert win._outer.contentsMargins().top() == 0                                  # rails and content are not pushed down
