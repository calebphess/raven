import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from raven2 import entry

ROOT = Path(__file__).resolve().parent.parent


class FakeLauncher:
    """Stands in for raven.launcher: V1 reads sys.argv[1:] and ends with sys.exit(<claude's exit code>)."""
    def __init__(self, code=0):
        self.argv_seen, self.code = None, code

    def main(self):
        self.argv_seen = list(sys.argv)
        sys.exit(self.code)


def install_launcher(monkeypatch, code=0):
    fake = FakeLauncher(code)
    monkeypatch.setitem(sys.modules, "raven.launcher", fake)
    import raven
    monkeypatch.setattr(raven, "launcher", fake, raising=False)
    return fake


def test_legacy_hands_the_rest_of_the_arguments_to_v1_untouched(monkeypatch):
    fake = install_launcher(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["something-else"])
    assert entry.main(["--legacy", "--resume", "abc123", "--model", "opus"]) == 0
    assert fake.argv_seen == ["raven", "--resume", "abc123", "--model", "opus"]       # claude flags still work


def test_legacy_with_no_arguments_runs_v1_plainly(monkeypatch):
    fake = install_launcher(monkeypatch)
    assert entry.main(["--legacy"]) == 0 and fake.argv_seen == ["raven"]


def test_legacy_returns_claudes_exit_code(monkeypatch):
    install_launcher(monkeypatch, code=3)
    assert entry.main(["--legacy"]) == 3


def test_legacy_without_the_v1_packages_explains_how_to_get_them(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "raven.launcher", None)        # makes `from raven import launcher` fail to import
    import raven
    monkeypatch.delattr(raven, "launcher", raising=False)
    assert entry.main(["--legacy"]) == 1
    err = capsys.readouterr().err
    assert "Legacy mode (V1)" in err and 'pip install -e ".[legacy]"' in err and "Traceback" not in err


def test_no_arguments_opens_the_app(monkeypatch):
    from raven2 import gui_main
    monkeypatch.setattr(gui_main, "main", lambda: 7)
    assert entry.main([]) == 7


def test_the_app_without_the_ui_packages_explains_how_to_get_them(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "raven2.gui_main", None)
    import raven2
    monkeypatch.delattr(raven2, "gui_main", raising=False)
    assert entry.main([]) == 1
    err = capsys.readouterr().err
    assert 'pip install -e ".[v2,ui]"' in err and "Traceback" not in err


def test_commands_are_forwarded_to_the_v2_cli_unchanged(monkeypatch):
    from raven2 import cli
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(list(argv)) or 5)
    assert entry.main(["--worker", "claude", "--cwd", "/x", "chat"]) == 5
    assert seen == [["--worker", "claude", "--cwd", "/x", "chat"]]


def test_legacy_flag_only_counts_as_the_first_argument(monkeypatch):
    from raven2 import cli
    fake = install_launcher(monkeypatch)
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(list(argv)) or 0)
    entry.main(["personas", "--legacy"])
    assert fake.argv_seen is None and seen == [["personas", "--legacy"]]


def test_help_mentions_legacy_and_then_shows_the_command_help(monkeypatch, capsys):
    from raven2 import cli
    monkeypatch.setattr(cli, "main", lambda argv=None: print("V2-CLI-HELP") or 0)
    assert entry.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "--legacy" in out and "original V1" in out and "V2-CLI-HELP" in out


def test_a_missing_package_inside_a_command_is_a_message_not_a_traceback(monkeypatch, capsys):
    from raven2 import cli
    def boom(argv=None):
        raise ModuleNotFoundError("No module named 'llama_cpp'", name="llama_cpp")
    monkeypatch.setattr(cli, "main", boom)
    assert entry.main(["chat"]) == 1
    err = capsys.readouterr().err
    assert "llama_cpp" in err and 'pip install -e ".[v2,ui]"' in err


# ── packaging metadata ──
def _pyproject():
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_console_scripts_make_raven_the_v2_entry_and_keep_everything_else():
    scripts = _pyproject()["project"]["scripts"]
    assert scripts["raven"] == "raven2.entry:main"
    assert scripts["raven2"] == "raven2.cli:main"
    assert scripts["raven-speak"] == "raven.speak:main" and scripts["raven-voice"] == "raven.voicecmd:main"   # V1 hooks/CLI intact


def test_extras_cover_each_mode_and_v2_lists_the_claude_sdk():
    proj = _pyproject()["project"]
    extras = proj["optional-dependencies"]
    assert {"v2", "ui", "app", "legacy"} <= set(extras)
    assert any(d.startswith("claude-agent-sdk") for d in extras["v2"])                     # was missing entirely
    assert any(d.startswith("elevenlabs") for d in extras["legacy"]) and any(d.startswith("pyperclip") for d in extras["legacy"])
    core = " ".join(proj["dependencies"])
    assert "elevenlabs" not in core and "pyperclip" not in core                           # V1-only packages are no longer forced on V2 users
    assert proj["version"] == "0.2.0"


def test_every_module_a_console_script_points_at_exists():
    for target in _pyproject()["project"]["scripts"].values():
        module = target.split(":")[0]
        assert importlib.util.find_spec(module) is not None, module


# ── end to end, as a real process ──
def run(args, **kw):
    return subprocess.run([sys.executable, "-m", "raven2.entry", *args], capture_output=True, text=True, timeout=120,
                          cwd=ROOT, **kw)


def test_command_works_as_a_real_process():
    r = run(["personas", "list"])
    assert r.returncode == 0 and "dottie" in r.stdout and "charles" in r.stdout


@pytest.mark.skipif(importlib.util.find_spec("elevenlabs") is not None, reason="V1 packages are installed here")
def test_legacy_in_an_environment_without_v1_packages_exits_cleanly():
    r = run(["--legacy"])
    assert r.returncode == 1 and "pip install" in r.stderr and "Traceback" not in r.stderr
