"""Claude Code as a hidden background worker via the Agent SDK.

- Isolation: RAVEN is blanked so the V1 hooks no-op, and the session gets a short appended system prompt
  that says it runs behind Raven. No persona text ever reaches Claude.
- A new send() interrupts the running turn first (verified in spikes/spike_sdk.py), then starts the new one.
- Permission requests surface as `ask` events and block until answer() — never auto-allowed.
"""

from __future__ import annotations

import asyncio
import threading

from ..interfaces import WorkerEvent

SESSION_PROMPT = (
    "You are running behind Raven, a voice assistant front-end. The user talks to Raven, which relays "
    "their messages to you and summarizes your output aloud. Keep updates brief and put the key result "
    "first. When you need a decision or approval, ask one short, direct question. Do not use any "
    "special tags or markup meant for speech."
)


def _tool_label(name: str, inp: dict) -> str:
    for k in ("file_path", "path", "command", "pattern", "url", "description"):
        if inp.get(k):
            return f"{name}: {str(inp[k])[:80]}"
    return name


def _speakable(name: str, inp: dict, title: str | None = None) -> str:
    """What Raven says aloud for an approval: short, no paths or code."""
    if title:
        return title
    base = lambda k: (str(inp.get(k, "")).rstrip("/").rsplit("/", 1)[-1] or "a file")
    verbs = {"Write": f"create {base('file_path')}", "Edit": f"edit {base('file_path')}",
             "MultiEdit": f"edit {base('file_path')}", "NotebookEdit": f"edit {base('notebook_path')}",
             "Read": f"read {base('file_path')}", "Glob": "search for files", "Grep": "search the code",
             "WebSearch": "search the web", "WebFetch": "fetch a web page"}
    if name == "Bash":
        return "Claude wants to run a shell command" + (f": {inp['description']}" if inp.get("description") else "")
    return "Claude wants to " + verbs.get(name, f"use the {name} tool")


class ClaudeWorker:
    def __init__(self, cwd: str = ".", model: str | None = None, permission_mode: str | None = None, **_):
        import os

        self._cwd = os.path.abspath(os.path.expanduser(cwd))
        if not os.path.isdir(self._cwd):  # fail at startup, not after the user has already spoken
            raise FileNotFoundError(f"working directory does not exist: {self._cwd}")
        self._model, self._mode = model, permission_mode
        self.on_event = None
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True, name="claude-worker").start()
        self._client = None
        self._turn = 0
        self._task: asyncio.Task | None = None
        self._pending: asyncio.Future | None = None
        self._always: set[str] = set()

    # ── public (thread-safe) ──
    def send(self, text: str) -> None:
        self._turn += 1
        asyncio.run_coroutine_threadsafe(self._send(text, self._turn), self._loop)

    def interrupt(self) -> None:
        asyncio.run_coroutine_threadsafe(self._interrupt(), self._loop).result(timeout=10)

    def answer(self, decision: str, text: str = "") -> bool:
        fut = self._pending
        if fut is None or fut.done():
            return False
        self._loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result((decision, text)))
        return True

    def has_pending(self) -> bool:
        return self._pending is not None and not self._pending.done()

    def close(self) -> None:
        async def _c():
            if self._client:
                await self._client.disconnect()
        try:
            asyncio.run_coroutine_threadsafe(_c(), self._loop).result(timeout=10)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    # ── internals (worker loop) ──
    def _emit(self, turn: int, kind: str, text: str = "", **data) -> None:
        if turn != self._turn or not self.on_event:  # drop output from superseded turns
            return
        self.on_event(WorkerEvent(kind, text, {"turn": turn, **data}))

    async def _ensure(self):
        if self._client is None:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

            opts = ClaudeAgentOptions(
                cwd=self._cwd,
                model=self._model,
                permission_mode=self._mode,
                can_use_tool=self._can_use_tool,
                env={"RAVEN": ""},
                system_prompt={"type": "preset", "preset": "claude_code", "append": SESSION_PROMPT},
            )
            self._client = ClaudeSDKClient(options=opts)
            await self._client.connect()
        return self._client

    async def _interrupt(self):
        if self._pending and not self._pending.done():
            self._pending.set_result(("deny", "The user sent a new message."))
        if self._client and self._task and not self._task.done():
            try:
                await self._client.interrupt()
            except Exception:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 10)
            except Exception:
                pass

    async def _send(self, text: str, turn: int):
        try:
            await self._interrupt()
            if turn != self._turn:  # a newer message already superseded this one
                return
            client = await self._ensure()
            await client.query(text)
            self._task = asyncio.ensure_future(self._drain(client, turn))
        except Exception as e:
            self._emit(turn, "error", f"{type(e).__name__}: {e}")

    async def _drain(self, client, turn: int):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

        try:
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for b in msg.content:
                        if isinstance(b, TextBlock) and b.text.strip():
                            self._emit(turn, "text", b.text.strip())
                        elif isinstance(b, ToolUseBlock):
                            self._emit(turn, "tool", _tool_label(b.name, b.input))
                elif isinstance(msg, ResultMessage):
                    self._emit(turn, "error" if msg.is_error else "done", (msg.result or "").strip())
        except Exception as e:
            self._emit(turn, "error", f"{type(e).__name__}: {e}")

    async def _can_use_tool(self, name, tool_input, ctx):
        from claude_agent_sdk import PermissionResultDeny

        try:
            return await self._decide(name, tool_input, ctx)
        except Exception as e:  # fail closed: a bug here must never turn into an allow
            self._emit(self._turn, "error", f"approval failed: {type(e).__name__}: {e}")
            return PermissionResultDeny(message="Raven could not ask the user; action not approved.")

    async def _decide(self, name, tool_input, ctx):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if name in self._always:
            return PermissionResultAllow()
        turn = self._turn
        self._pending = fut = self._loop.create_future()
        if name == "AskUserQuestion":
            qs = tool_input.get("questions") or []
            label = " ".join(q.get("question", "") for q in qs) or "Claude has a question."
            self._emit(turn, "ask", label, ask_type="question", tool=name)
        else:
            self._emit(turn, "ask", _speakable(name, tool_input, ctx.title), ask_type="permission", tool=name,
                       detail=_tool_label(name, tool_input))
        decision, text = await fut
        if name == "AskUserQuestion":  # answer comes back as feedback text
            return PermissionResultDeny(message=f"The user answered: {text}")
        if decision == "always":
            self._always.add(name)
        if decision in ("allow", "always"):
            return PermissionResultAllow()
        return PermissionResultDeny(message=text or "The user denied this action.")
