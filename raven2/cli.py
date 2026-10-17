from __future__ import annotations

import argparse
import os
import sys

from . import config, packs, personas as P


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="raven2")
    ap.add_argument("--profile")
    ap.add_argument("--worker", choices=["null", "claude"], help="background agent (default: from profile; `claude` = Claude Code)")
    ap.add_argument("--cwd", help="directory Claude works in (default: current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("personas", help="list / use / install / remove personas")
    pp.add_argument("action", choices=["list", "use", "install", "remove", "fetch"], nargs="?", default="list")
    pp.add_argument("arg", nargs="?")
    sub.add_parser("run", help="push-to-talk voice loop (hold Right Option)")
    sub.add_parser("chat", help="typed input, spoken replies (no mic needed)")
    sp = sub.add_parser("say", help="speak a line in a persona's voice")
    sp.add_argument("text")
    sp.add_argument("--persona")
    sp.add_argument("--raw", action="store_true", help="skip persona effects and pitch (plain base voice)")
    sub.add_parser("profile", help="show the resolved profile")

    a = ap.parse_args(argv)
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

    try:
        asst = app.build(a.profile, getattr(a, "persona", None), worker=a.worker,
                         cwd=(a.cwd or os.getcwd()) if a.worker == "claude" else a.cwd)
    except FileNotFoundError as e:
        print(f"[raven] {e}", file=sys.stderr)
        return 1
    try:
        asst.prepare()
    except packs.PackError as e:
        print(f"[raven] {e}", file=sys.stderr)
        return 1
    if a.cmd == "say":
        if a.raw:
            asst.persona.voice.effects = {}
            asst.persona.voice.params.pop("length_scale", None)
        asst.say(a.text)
        asst.wait()
    elif a.cmd == "chat":
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

        try:
            ptt.run(asst)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
