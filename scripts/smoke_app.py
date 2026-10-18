"""Launch the packaged dist/Raven.app headless with a Finder-like environment and report what happened.

Safety rails (a frozen app that re-launches itself is a fork bomb): the app runs in its own process group,
the whole group is killed on timeout, and the app's own process creation is capped.

Usage: python scripts/smoke_app.py [seconds=60] [--missing-claude] [--selftest "message"]"""
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "dist" / "Raven.app" / "Contents" / "MacOS" / "Raven"


def user_process_count() -> int:
    out = subprocess.run(["ps", "-u", str(os.getuid()), "-o", "pid="], capture_output=True, text=True).stdout
    return len(out.split())


def main(seconds: int, missing_claude: bool, selftest: str | None = None) -> int:
    home = Path.home()
    sandbox = home / ".raven" / "smoke"
    (sandbox / "logs").mkdir(parents=True, exist_ok=True)
    (sandbox / "logs" / "app.log").write_text("")
    if not (sandbox / "models").exists():
        (sandbox / "models").symlink_to(home / ".raven" / "models")
    proj = sandbox / "project"
    proj.mkdir(exist_ok=True)
    (sandbox / "state.json").write_text('{"project_dir": "%s"}' % proj)
    fake_home = sandbox / "fakehome"          # an empty home: no ~/.local/bin/claude to find
    fake_home.mkdir(exist_ok=True)
    env = {"HOME": str(fake_home if missing_claude else home), "USER": os.environ.get("USER", ""), "LANG": "en_US.UTF-8",
           "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",           # what a Finder-launched app gets
           "SHELL": "/usr/bin/false" if missing_claude else os.environ.get("SHELL", "/bin/zsh"),
           "RAVEN_HOME": str(sandbox), "QT_QPA_PLATFORM": "offscreen", "RAVEN_NO_PTT": "1"}
    if selftest:
        env["RAVEN_SELFTEST"] = selftest
    cap = user_process_count() + 25

    def limit():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_NPROC, (cap, cap))   # forks beyond this fail instead of piling up

    p = subprocess.Popen([str(BIN)], env=env, preexec_fn=limit, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        t0 = time.time()
        while time.time() - t0 < seconds and p.poll() is None:
            time.sleep(1)
        alive = p.poll() is None
        peak = user_process_count() - (cap - 25)
    finally:
        try:
            os.killpg(p.pid, signal.SIGINT if p.poll() is None else signal.SIGKILL)
            time.sleep(4)
            os.killpg(p.pid, signal.SIGKILL)               # whatever is left of the group
        except (ProcessLookupError, PermissionError):  # group already gone (macOS says EPERM for that)
            pass
    out = p.stdout.read() if p.stdout else ""
    log = (sandbox / "logs" / "app.log").read_text()
    print(f"alive after {seconds}s: {alive} | exit code: {p.returncode} | process count change at the end: {peak:+d}")
    print("--- app.log ---\n" + log.strip()[-2500:])
    noise = ("qt.qpa", "propagateSize")
    print("--- output ---\n" + "\n".join(l for l in out.splitlines() if not any(n in l for n in noise))[-1500:])
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    st = sys.argv[sys.argv.index("--selftest") + 1] if "--selftest" in sys.argv else None
    args = [a for a in args if a != st]
    raise SystemExit(main(int(args[0]) if args else 60, "--missing-claude" in sys.argv, st))
