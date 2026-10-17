from __future__ import annotations

import argparse
import os
import sys

from . import config, packs, personas as P
from .core import StartupError


def _list_personas() -> int:
    allp, active = P.load_all(), P.get_active_id()
    for p in allp.values():
        mark = "*" if p.id == active else " "
        print(f"{mark} {p.id:<10} {p.name:<10} [{p.source}] {p.description}")
    print("\n(* active)  switch: raven2 personas use <id>   add: raven2 personas install <path|url|name>")
    return 0


def _personas(a) -> int:
    if a.action == "list":
        return _list_personas()
    if a.action == "use":
        p = P.find(a.arg or "", P.load_all())
        if not p:
            print(f"no such persona: {a.arg}", file=sys.stderr)
            return 1
        P.set_active_id(p.id)
        print(f"active persona: {p.name}")
        return 0
    if a.action == "install":
        p = P.install(a.arg)
        print(f"installed {p.name} ({p.id}). Switch with: raven2 personas use {p.id}")
        return 0
    if a.action == "remove":
        print("removed" if P.remove(a.arg) else "not an installed persona (built-ins can't be removed)")
        return 0
    if a.action == "fetch":  # download packs for all personas (or one) ahead of time
        from .backends.piper_tts import ensure_voice
        from .interfaces import AssetMissing

        allp = P.load_all()
        chosen = [P.find(a.arg, allp)] if a.arg else list(allp.values())
        for p in chosen:
            if p is None:
                print(f"no such persona: {a.arg}", file=sys.stderr)
                return 1
            try:
                ensure_voice(p.voice.model)
            except AssetMissing:
                packs.install_pack(p.id, p.name)
        return 0
    return 1


def _startup(asst, a) -> int:
    try:
        asst.startup(intro=not a.no_intro)
    except StartupError as e:
        print(f"[raven] startup failed: {e}", file=sys.stderr)
        return 1
    return 0


def _show_status(asst) -> None:
    from .status import Status

    st = Status()
    if st.enabled:
        st.start()
        asst.status = st


def detached_argv(argv: list[str]) -> list[str]:
    """The command that re-runs this same invocation in the background (without --detach)."""
    return [sys.executable, "-m", "raven2.cli", *[x for x in argv if x != "--detach"]]


def _detach(argv: list[str]) -> int:
    import subprocess

    log = config.HOME / "logs" / "ui.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "RAVEN"}
    with open(log, "ab") as f:
        p = subprocess.Popen(detached_argv(argv), stdin=subprocess.DEVNULL, stdout=f, stderr=f, env=env,
                             start_new_session=True)  # its own session: closing this terminal won't kill it
    print(f"Raven UI started in the background (pid {p.pid}). Log: {log}\nQuit it from its window or the menubar icon.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="raven2")
    ap.add_argument("--profile")
    ap.add_argument("--worker", choices=["null", "claude"], help="background agent (default: from profile; `claude` = Claude Code)")
    ap.add_argument("--cwd", help="directory Claude works in (default: current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("personas", help="list / use / install / remove personas")
    pp.add_argument("action", choices=["list", "use", "install", "remove", "fetch"], nargs="?", default="list")
    pp.add_argument("arg", nargs="?")
    for name, h in (("run", "push-to-talk voice loop (hold Right Option)"), ("chat", "typed input, spoken replies (no mic needed)")):
        sub.add_parser(name, help=h).add_argument("--no-intro", action="store_true", help="skip the spoken startup greeting")
    up = sub.add_parser("ui", help="menubar app + window; closing the window quits")
    up.add_argument("--detach", action="store_true", help="run in the background and give the terminal back")
    sp = sub.add_parser("say", help="speak a line in a persona's voice")
    sp.add_argument("text")
    sp.add_argument("--persona")
    sp.add_argument("--raw", action="store_true", help="skip persona effects and pitch (plain base voice)")
    sub.add_parser("profile", help="show the resolved profile")

    raw = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(raw)
    if a.cmd == "ui" and a.detach:
        return _detach(raw)
    if a.cmd == "personas":
        try:
            return _personas(a)
        except packs.PackError as e:
            print(f"[raven] {e}", file=sys.stderr)
            return 1
    if a.cmd == "profile":
        import json

        print(json.dumps(config.load_profile(a.profile), indent=2))
        return 0

    from . import app

    worker = a.worker or ("claude" if a.cmd == "ui" else None)  # the UI exists to front Claude Code
    try:
        asst = app.build(a.profile, getattr(a, "persona", None), worker=worker,
                         cwd=(a.cwd or os.getcwd()) if worker == "claude" else a.cwd)
    except FileNotFoundError as e:
        print(f"[raven] {e}", file=sys.stderr)
        return 1
    try:
        asst.prepare()
    except packs.PackError as e:
        print(f"[raven] {e}", file=sys.stderr)
        return 1
    if a.cmd == "ui":
        from .ui import run_ui

        return run_ui(asst)
    if a.cmd == "say":
        if a.raw:
            asst.persona.voice.effects = {}
            asst.persona.voice.params.pop("length_scale", None)
        asst.say(a.text)
        asst.wait()
    elif a.cmd == "chat":
        if _startup(asst, a):
            return 1
        _show_status(asst)
        print(f"{asst.persona.name} ready. Type to talk; 'quit' exits. Try: list personas / switch to charles")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if line in ("quit", "exit"):
                break
            if line:
                asst.handle_text(line)
                asst.wait()
    elif a.cmd == "run":
        from . import ptt

        if _startup(asst, a):
            return 1
        _show_status(asst)
        try:
            ptt.run(asst)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
