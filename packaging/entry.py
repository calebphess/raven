"""PyInstaller entry point for Raven.app."""
import multiprocessing
import os
import sys
import time


def _nested_launch() -> bool:
    """True if this process was started by an already-running Raven (a library re-executing the frozen binary).
    Running a second app from there recurses into a fork bomb, so refuse, and say so in the log."""
    parent = os.environ.get("RAVEN_APP_PID")
    return bool(parent) and parent != str(os.getpid())


if __name__ == "__main__":
    multiprocessing.freeze_support()  # frozen multiprocessing children must run as workers, not as the app
    if _nested_launch():
        try:
            home = os.environ.get("RAVEN_HOME") or os.path.expanduser("~/.raven")
            os.makedirs(os.path.join(home, "logs"), exist_ok=True)
            with open(os.path.join(home, "logs", "app.log"), "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} WARNING refused nested launch: "
                        f"pid={os.getpid()} parent_app_pid={os.environ.get('RAVEN_APP_PID')} argv={sys.argv!r}\n")
        except Exception:
            pass
        sys.exit(0)
    os.environ["RAVEN_APP_PID"] = str(os.getpid())
    from raven2.gui_main import main

    sys.exit(main())
