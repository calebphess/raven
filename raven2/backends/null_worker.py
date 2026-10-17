from __future__ import annotations


class NullWorker:
    """No background agent: Raven just chats. Accepts messages and does nothing."""

    background = False  # nothing runs behind the scenes, so the front brain answers itself

    def __init__(self, **_):
        self.sent: list[str] = []
        self.on_event = None
        self.controls = None
        self.detached = False

    def send(self, text: str) -> None:
        self.sent.append(text)

    def interrupt(self) -> None:
        pass

    def answer(self, decision: str, text: str = "") -> bool:
        return False

    def has_pending(self) -> bool:
        return False

    def close(self) -> None:
        pass

    def session_info(self):
        return None

    def detach(self) -> None:
        pass

    def attach(self) -> None:
        pass

    def open_project(self, cwd: str) -> None:
        pass
