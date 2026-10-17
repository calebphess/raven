import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from raven2 import config, dialogs, gui_main, preflight as pf


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ── "Install Claude Code first" ──
def test_missing_claude_dialog_explains_and_offers_the_install(qapp):
    d = dialogs.claude_missing_dialog()
    assert set(d.buttons) == {"quit", "guide", "copy", "again"}
    texts = " ".join(l.text() for l in d.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))
    assert "Install Claude Code first" in texts and pf.INSTALL_COMMAND in texts
    assert d.buttons["again"].isDefault()                          # the expected next step after installing


def test_copy_button_copies_the_install_command_and_keeps_the_dialog_open(qapp):
    d = dialogs.claude_missing_dialog()
    d.buttons["copy"].click()
    assert QApplication.clipboard().text() == pf.INSTALL_COMMAND
    assert d.result_key == "quit" and d.buttons["copy"].text().startswith("Copied")


def test_guide_button_opens_the_install_page(qapp, monkeypatch):
    opened = []
    monkeypatch.setattr(dialogs.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))
    dialogs.claude_missing_dialog().buttons["guide"].click()
    assert opened == [pf.INSTALL_URL]


def test_again_and_quit_set_the_result(qapp):
    for key in ("again", "quit"):
        d = dialogs.claude_missing_dialog()
        d.buttons[key].click()
        assert d.result_key == key


def test_models_missing_dialog_lists_what_is_missing(qapp):
    from PySide6.QtWidgets import QLabel
    d = dialogs.models_missing_dialog(["language model (x.gguf)", "speech-to-text model (whisper base)"], "/m")
    text = " ".join(l.text() for l in d.findChildren(QLabel))
    assert "language model (x.gguf)" in text and "whisper base" in text and "/m" in text


# ── the startup sequence ──
class FakeDialog:
    def __init__(self, key): self.result_key = key
    def exec(self): return 0


def test_ensure_claude_loops_until_installed(qapp, monkeypatch):
    found = [None, None, pf.ClaudeInfo("/bin/claude", "9.9")]
    monkeypatch.setattr(pf, "find_claude", lambda: found.pop(0))
    keys = iter(["again", "again"])
    monkeypatch.setattr("raven2.dialogs.claude_missing_dialog", lambda: FakeDialog(next(keys)))
    assert gui_main.ensure_claude(qapp).path == "/bin/claude" and found == []


def test_ensure_claude_stops_when_the_user_quits(qapp, monkeypatch):
    monkeypatch.setattr(pf, "find_claude", lambda: None)
    monkeypatch.setattr("raven2.dialogs.claude_missing_dialog", lambda: FakeDialog("quit"))
    assert gui_main.ensure_claude(qapp) is None


def test_installed_claude_never_shows_a_dialog(qapp, monkeypatch):
    monkeypatch.setattr(pf, "find_claude", lambda: pf.ClaudeInfo("/bin/claude", "9.9"))
    def boom(): raise AssertionError("dialog shown although Claude Code is installed")
    monkeypatch.setattr("raven2.dialogs.claude_missing_dialog", boom)
    assert gui_main.ensure_claude(qapp).version == "9.9"






def test_state_updates_merge_instead_of_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    config.update_state(persona="charles")
    config.update_state(project_dir="/x")
    assert config.read_state() == {"persona": "charles", "project_dir": "/x"}


def test_finder_launch_environment_is_repaired(monkeypatch, tmp_path):
    (tmp_path / ".local/bin").mkdir(parents=True)
    monkeypatch.setattr(pf, "candidate_dirs", lambda home=None: [str(tmp_path / ".local/bin"), "/usr/bin"])
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("RAVEN", "1")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    gui_main.setup_environment()
    parts = os.environ["PATH"].split(":")
    assert parts[0] == str(tmp_path / ".local/bin") and len(parts) == len(set(parts))   # added, no duplicates
    assert "RAVEN" not in os.environ and os.environ["HF_HUB_OFFLINE"] == "1"


def test_main_does_not_start_anything_when_claude_is_missing(monkeypatch, qapp):
    built = []
    monkeypatch.setattr(gui_main, "setup_environment", lambda: None)
    monkeypatch.setattr(gui_main, "ensure_claude", lambda app: None)
    monkeypatch.setattr("raven2.app.build", lambda *a, **k: built.append(1))
    assert gui_main.main() == 0 and built == []


def test_main_stops_with_a_dialog_when_models_are_missing(monkeypatch, qapp):
    shown, built = [], []
    monkeypatch.setattr(gui_main, "setup_environment", lambda: None)
    monkeypatch.setattr(gui_main, "ensure_claude", lambda app: pf.ClaudeInfo("/bin/claude", "9"))
    monkeypatch.setattr(pf, "missing_models", lambda *a: ["language model (x)"])
    monkeypatch.setattr("raven2.dialogs.models_missing_dialog",
                        lambda m, d: type("D", (), {"exec": lambda self: shown.append(m)})())
    monkeypatch.setattr("raven2.app.build", lambda *a, **k: built.append(1))
    assert gui_main.main() == 1 and shown == [["language model (x)"]] and built == []


def test_main_starts_in_the_home_folder_without_asking_and_passes_the_found_claude(monkeypatch, qapp, tmp_path):
    seen = {}
    monkeypatch.setattr(gui_main, "setup_environment", lambda: None)
    monkeypatch.setattr(gui_main, "request_input_monitoring", lambda: None)
    monkeypatch.setattr(gui_main, "ensure_claude", lambda app: pf.ClaudeInfo("/odd/place/claude", "9"))
    monkeypatch.setattr(pf, "missing_models", lambda *a: [])
    monkeypatch.setattr("raven2.app.build", lambda **k: seen.update(k) or "assistant")
    monkeypatch.setattr("raven2.ui.run_ui", lambda asst, **k: (seen.update(ui=(asst, k)), 0)[1])
    assert gui_main.main() == 0
    from pathlib import Path
    home = str(Path.home())
    assert seen["claude_path"] == "/odd/place/claude" and seen["cwd"] == home and seen["worker"] == "claude"
    assert seen["ui"][0] == "assistant" and seen["ui"][1]["project_dir"] == home


def test_dialog_buttons_are_wide_enough_for_their_labels(qapp):
    """Regression: 'Open install guide' and 'Copy command' were clipped to 'pen install gui' / 'opy comman'."""
    d = dialogs.claude_missing_dialog()
    d.show(); qapp.processEvents()
    for b in d.buttons.values():
        assert b.width() >= b.fontMetrics().horizontalAdvance(b.text()) + 30, b.text()


# ── the packaged app must never relaunch itself recursively ──
def _load_entry():
    import importlib.util
    spec = importlib.util.spec_from_file_location("raven_entry", os.path.join(os.path.dirname(__file__), "..", "packaging", "entry.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)           # defines helpers only: the launch code sits under __main__
    return mod


def test_entry_detects_a_launch_by_an_already_running_copy(monkeypatch):
    entry = _load_entry()
    monkeypatch.delenv("RAVEN_APP_PID", raising=False)
    assert entry._nested_launch() is False                       # a normal launch (Finder, Dock, terminal)
    monkeypatch.setenv("RAVEN_APP_PID", "99999")
    assert entry._nested_launch() is True                        # spawned by a running Raven -> must not start
    monkeypatch.setenv("RAVEN_APP_PID", str(os.getpid()))
    assert entry._nested_launch() is False                       # the app itself


def test_nested_launch_exits_without_starting_the_app_and_logs_why(tmp_path):
    import subprocess, sys
    env = {**os.environ, "RAVEN_APP_PID": "99999", "RAVEN_HOME": str(tmp_path), "QT_QPA_PLATFORM": "offscreen"}
    entry = os.path.join(os.path.dirname(__file__), "..", "packaging", "entry.py")
    r = subprocess.run([sys.executable, entry], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "Traceback" not in r.stderr
    log = (tmp_path / "logs" / "app.log").read_text()
    assert "refused nested launch" in log and "parent_app_pid=99999" in log
    assert "starting Raven" not in log                           # it never reached the real startup




def test_diagnostics_report_the_native_pieces(qapp):
    d = gui_main.diagnostics()
    assert set(d) == {"audio_devices", "pynput", "input_monitoring_granted"}
    assert d["pynput"] == "ok"


def test_selftest_sends_one_message_logs_events_and_quits(qapp, monkeypatch, tmp_path):
    from test_v2 import make_with_worker
    from raven2.backends.null_worker import NullWorker
    from raven2.interfaces import WorkerEvent
    from raven2.ui import Bridge, _selftest
    import logging
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    bridge, quits, records = Bridge(), [], []
    bridge.quit_req.connect(lambda: quits.append(1))
    class Capture(logging.Handler):
        def emit(self, r): records.append(r.getMessage())
    h = Capture(); logging.getLogger("raven").addHandler(h); logging.getLogger("raven").setLevel(logging.INFO)
    orig_send = w.send
    w.send = lambda text: (orig_send(text), w.on_event(WorkerEvent("say", "Four.")), w.on_event(WorkerEvent("done", "Four.")))
    try:
        _selftest(a, bridge, "what is two plus two please")
        qapp.processEvents()
    finally:
        logging.getLogger("raven").removeHandler(h)
    text = "\n".join(records)
    assert "selftest: sending 'what is two plus two please'" in text and "selftest event: say 'Four.'" in text
    assert "selftest: finished=True" in text and quits == [1] and w.sent == ["what is two plus two please"]


def test_launch_never_asks_for_a_folder(monkeypatch, qapp):
    """Opening Raven goes straight to the home folder: no folder dialog on launch."""
    from PySide6.QtWidgets import QFileDialog
    for fn in ("getExistingDirectory", "getSaveFileName", "getOpenFileName"):
        monkeypatch.setattr(QFileDialog, fn, staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked for a folder"))))
    monkeypatch.setattr(gui_main, "setup_environment", lambda: None)
    monkeypatch.setattr(gui_main, "request_input_monitoring", lambda: None)
    monkeypatch.setattr(gui_main, "ensure_claude", lambda app: pf.ClaudeInfo("/bin/claude", "9"))
    monkeypatch.setattr(pf, "missing_models", lambda *a: [])
    monkeypatch.setattr("raven2.app.build", lambda **k: "assistant")
    monkeypatch.setattr("raven2.ui.run_ui", lambda asst, **k: 0)
    assert gui_main.main() == 0
    assert not hasattr(gui_main, "ensure_project_dir") and not hasattr(gui_main, "change_folder")
