"""Always-visible status line for the background worker.

Pins one line to the bottom of the terminal (a scroll region keeps logs above it) and mirrors it in the
terminal tab title. Two layers: the *worker* state (idle / working / waiting for you / done / error) and a
short-lived *overlay* for the mic (listening / transcribing). Disabled automatically when not a TTY.
"""

from __future__ import annotations

import atexit
import shutil
import sys
import threading
import time

_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_C = {"dim": "\033[2m", "yellow": "\033[33;1m", "green": "\033[32;1m", "red": "\033[31;1m",
      "magenta": "\033[35;1m", "cyan": "\033[36;1m", "off": "\033[0m"}


class Status:
    def __init__(self, stream=None, enabled: bool | None = None):
        self.out = stream or sys.stdout
        self.enabled = self.out.isatty() if enabled is None else enabled
        self.state, self.detail = "idle", ""
        self.tools = 0
        self.overlay = ""  # "listening" | "transcribing" | ""
        self._t0 = 0.0
        self._elapsed = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._rows = 0
        self._thread: threading.Thread | None = None

    # ── state changes (thread-safe, cheap) ──
    def working(self, fresh: bool = False) -> None:
        """fresh=True: a new request (restarts the timer). False: the same run carrying on."""
        with self._lock:
            if fresh or self.state not in ("working", "waiting"):
                self._t0, self.tools = time.monotonic(), 0
            self.state, self.detail = "working", ""
        self._redraw()

    def tool(self) -> None:
        with self._lock:
            self.tools += 1
        self._redraw()

    def waiting(self, detail: str = "") -> None:
        with self._lock:
            self.state, self.detail = "waiting", detail
        self._redraw()

    def done(self) -> None:
        with self._lock:
            self._elapsed = time.monotonic() - self._t0 if self._t0 else 0.0
            self.state, self.detail = "done", ""
        self._redraw()

    def error(self, detail: str = "") -> None:
        with self._lock:
            self.state, self.detail = "error", detail
        self._redraw()

    def manual(self) -> None:
        with self._lock:
            self.state, self.detail = "manual", ""
        self._redraw()

    def idle(self) -> None:
        with self._lock:
            self.state, self.detail = "idle", ""
        self._redraw()

    def listen(self, what: str = "") -> None:
        with self._lock:
            self.overlay = what
        self._redraw()

    # ── rendering ──
    def text(self) -> tuple[str, str]:
        """(colored line, plain title) for the current state."""
        with self._lock:
            if self.overlay:
                label = {"listening": "● listening…", "transcribing": "◌ transcribing…"}[self.overlay]
                return f"{_C['cyan']}{label}{_C['off']}", f"Raven: {label}"
            s, d = self.state, self.detail
            run = time.monotonic() - self._t0 if self._t0 else 0.0
            tools = f" · {self.tools} tool{'s' if self.tools != 1 else ''}" if self.tools else ""
            if s == "working":
                sp = _SPIN[int(time.monotonic() * 10) % len(_SPIN)]
                label = f"{sp} Claude working {run:.0f}s{tools}"
                return f"{_C['yellow']}{label}{_C['off']}", f"Raven: {label}"
            if s == "manual":
                label = "◆ Claude is open in a terminal — Raven is paused"
                return f"{_C['cyan']}{label}{_C['off']}", f"Raven: {label}"
            if s == "waiting":
                label = f"? Claude is waiting for your answer{': ' + d if d else ''}"
                return f"{_C['magenta']}{label}{_C['off']}", f"Raven: {label}"
            if s == "done":
                label = f"✓ Claude finished in {self._elapsed:.0f}s{tools}"
                return f"{_C['green']}{label}{_C['off']}", f"Raven: {label}"
            if s == "error":
                label = f"✗ Claude error{': ' + d if d else ''}"
                return f"{_C['red']}{label}{_C['off']}", f"Raven: {label}"
            return f"{_C['dim']}○ ready{_C['off']}", "Raven: ready"

    def render_once(self) -> str:
        """The escape sequence that paints the status line (also used by tests)."""
        rows = shutil.get_terminal_size((80, 24)).lines
        line, title = self.text()
        cols = shutil.get_terminal_size((80, 24)).columns
        return (f"\0337\033[{rows};1H\033[2K {line}\033[0m\0338" f"\033]0;{title}\007")[: cols * 4 + 64]

    def _redraw(self) -> None:
        if not self.enabled:
            return
        try:
            rows = shutil.get_terminal_size((80, 24)).lines
            if rows != self._rows:  # first draw or the window was resized: re-reserve the bottom line
                self._rows = rows
                self.out.write(f"\033[1;{rows - 1}r")
            self.out.write(self.render_once())
            self.out.flush()
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._stop.wait(0.12):
            if self.state in ("working",) or self.overlay:
                self._redraw()

    def start(self) -> None:
        if not self.enabled or self._thread:
            return
        rows = shutil.get_terminal_size((80, 24)).lines
        self.out.write("\n" + f"\033[1;{rows - 1}r" + f"\033[{rows - 1};1H")  # reserve the last line
        self._rows = rows
        self._redraw()
        atexit.register(self.stop)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="status")
        self._thread.start()

    def stop(self) -> None:
        if not self.enabled or self._stop.is_set():
            return
        self._stop.set()
        try:
            rows = shutil.get_terminal_size((80, 24)).lines
            self.out.write(f"\033[r\033[{rows};1H\033[2K\033]0;\007\n")  # release the region, clear the line
            self.out.flush()
        except Exception:
            pass
