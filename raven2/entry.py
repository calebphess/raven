"""The `raven` command.

    raven                      open the Raven app (V2)
    raven <command> [...]      V2 commands: ui, run, chat, say, personas, profile (same as `raven2 ...`)
    raven --legacy [args]      the original V1 voice launcher; args go to `claude` (e.g. --resume)

V1 stays exactly as it was (ElevenLabs voices and Whisper typing into a visible Claude Code terminal, driven by
hooks); only the way you start it changed.
"""

from __future__ import annotations

import sys

HELP = """usage: raven [command ...]

  raven                     open the Raven app
  raven <command> ...       run a command: ui, run, chat, say, personas, profile   (try: raven personas list)
  raven --legacy [args]     the original V1 voice launcher; [args] are passed to `claude` (e.g. --resume)
"""

NEEDS_V2 = ("Raven's app needs the V2 packages. Install them with:\n"
            "    pip install -e \".[v2,ui]\"\n(missing: {missing})")
NEEDS_LEGACY = ("Legacy mode (V1) needs the V1 packages. Install them with:\n"
                "    pip install -e \".[legacy]\"\n(missing: {missing})")


def _missing(e: ImportError) -> str:
    return getattr(e, "name", None) or str(e)


def _legacy(rest: list[str]) -> int:
    try:
        from raven import launcher  # V1; importing it pulls in ElevenLabs, pyperclip and Whisper
    except ImportError as e:
        print(NEEDS_LEGACY.format(missing=_missing(e)), file=sys.stderr)
        return 1
    sys.argv = ["raven", *rest]  # V1 forwards sys.argv[1:] to claude
    try:
        launcher.main()
    except SystemExit as e:  # V1 ends with sys.exit(<claude's exit code>)
        return e.code if isinstance(e.code, int) else 0
    return 0


def _app() -> int:
    try:
        from . import gui_main
    except ImportError as e:
        print(NEEDS_V2.format(missing=_missing(e)), file=sys.stderr)
        return 1
    return gui_main.main()


def _command(argv: list[str]) -> int:
    from . import cli

    try:
        return cli.main(argv)
    except ImportError as e:
        print(NEEDS_V2.format(missing=_missing(e)), file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--legacy":
        return _legacy(argv[1:])
    if not argv:
        return _app()
    if argv[0] in ("-h", "--help"):
        print(HELP)
        return _command(["--help"])
    return _command(argv)


if __name__ == "__main__":
    raise SystemExit(main())
