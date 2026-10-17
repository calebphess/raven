import os
import stat

import pytest

from raven2 import config, preflight as pf


def fake_claude(path, body='echo "9.9.9 (Claude Code)"', code=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\nexit {code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_finds_claude_in_a_common_install_dir_when_path_is_empty(tmp_path):
    exe = fake_claude(tmp_path / ".local/bin/claude")        # where the native installer puts it
    info = pf.find_claude(env={"PATH": ""}, home=tmp_path, login_shell=False)
    assert info.path == exe and info.version == "9.9.9 (Claude Code)"


def test_finds_npm_global_and_nvm_installs(tmp_path):
    exe = fake_claude(tmp_path / ".nvm/versions/node/v20.1.0/bin/claude")
    assert pf.find_claude(env={"PATH": ""}, home=tmp_path, login_shell=False).path == exe


def test_path_hit_wins_over_the_fallback_dirs(tmp_path):
    on_path = fake_claude(tmp_path / "bin/claude", 'echo "1.0.0 (Claude Code)"')
    fake_claude(tmp_path / ".local/bin/claude", 'echo "2.0.0 (Claude Code)"')
    info = pf.find_claude(env={"PATH": str(tmp_path / "bin")}, home=tmp_path, login_shell=False)
    assert info.path == on_path and info.version.startswith("1.0.0")


def test_explicit_override_wins(tmp_path):
    mine = fake_claude(tmp_path / "custom/claude", 'echo "7.7.7 (Claude Code)"')
    fake_claude(tmp_path / ".local/bin/claude")
    info = pf.find_claude(env={"PATH": "", "RAVEN_CLAUDE": mine}, home=tmp_path, login_shell=False)
    assert info.path == mine


def test_a_broken_install_does_not_count(tmp_path):
    fake_claude(tmp_path / ".local/bin/claude", 'echo "boom" >&2', code=1)       # exits non-zero
    (tmp_path / ".claude/local").mkdir(parents=True)
    (tmp_path / ".claude/local/claude").write_text("not executable")             # not executable at all
    assert pf.find_claude(env={"PATH": ""}, home=tmp_path, login_shell=False) is None


def test_not_installed_returns_none(tmp_path):
    assert pf.find_claude(env={"PATH": ""}, home=tmp_path / "nothing-here", login_shell=False) is None


def test_login_shell_is_the_last_resort(tmp_path, monkeypatch):
    exe = fake_claude(tmp_path / "odd/place/claude")
    monkeypatch.setattr(pf, "_login_shell_lookup", lambda env: exe)
    assert pf.find_claude(env={"PATH": ""}, home=tmp_path / "x").path == exe
    monkeypatch.setattr(pf, "_login_shell_lookup", lambda env: None)
    assert pf.find_claude(env={"PATH": ""}, home=tmp_path / "x") is None


def test_login_shell_output_noise_is_ignored(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, "Last login: Sat\nwelcome back!\n/Users/me/.local/bin/claude\n", ""))
    assert pf._login_shell_lookup({"SHELL": "/bin/zsh"}) == "/Users/me/.local/bin/claude"


def test_install_hints_are_present():
    assert pf.INSTALL_COMMAND.startswith("curl") and pf.INSTALL_URL.startswith("https://")


def test_missing_models_reports_what_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "models")
    profile = {"llm": {"model": "tiny.gguf"}, "stt": {"model": "base"}}
    out = pf.missing_models(profile)
    assert len(out) == 2 and "language model" in out[0] and "speech-to-text" in out[1]
    (tmp_path / "models").mkdir()
    (tmp_path / "models/tiny.gguf").write_text("x")
    (tmp_path / "models/whisper/models--Systran--faster-whisper-base/snapshots/abc").mkdir(parents=True)
    assert pf.missing_models(profile) == []
