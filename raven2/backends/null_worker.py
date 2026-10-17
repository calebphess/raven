from __future__ import annotations


class NullWorker:
    """No background agent: Raven just chats. Accepts messages and does nothing."""

    background = False  # nothing runs behind the scenes, so the front brain answers itself

    def __init__(self, **_):
        self.sent: list[str] = []
        self.on_event = None

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
