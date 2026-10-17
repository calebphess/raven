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
from ..raventags import split_raven, strip_tags

SESSION_PROMPT = (
    "You are running behind Raven, a voice assistant. The user is LISTENING, not reading: they essentially "
    "never look at your terminal text. Everything they need to hear or review MUST be inside "
    "<raven>...</raven> tags. Anything outside the tags is only a log they will not see, and your tool "
    "calls are summarized for them separately by Raven, so do not narrate your actions. Raven speaks the "
    "text inside <raven> tags verbatim in the active persona's voice, so write it exactly as it should be "
    "said aloud: plain, conversational sentences; no markdown, lists, code, file paths or URLs (describe "
    "them instead); numbers the way you would say them. Write it in the voice of the persona described in "
    "the persona note Raven attaches as context to each message (tone only; keep facts exact). You may "
    "use several <raven> blocks in one turn (for example a quick heads-up before a long task), but always "
    "END the turn with a <raven> block that contains the result or your question.\n\n"
    "BE BRIEF. HARD LIMIT: each <raven> block is at most about 35 words (three short sentences) unless the "
    "user explicitly asks for detail. If your draft is longer, cut it to the single most important point and "
    "offer more. Never spell out file names, paths, extensions or symbols (no 'slash' or 'dot'); say 'the "
    "status module' or 'the command line file'. Examples. User: 'What does the status module do?' -> "
    "<raven>It shows a one-line status bar at the bottom of the terminal telling you whether Claude is "
    "working, waiting, or finished. Want the details?</raven> User: 'Plan how to add a version flag.' -> "
    "<raven>It's a small change: add one argument to the command line parser, read the version from the "
    "package, and add a test. Want me to walk through it step by step?</raven>\n\n"
    "If a complete answer cannot be brief (long explanations, step-by-step instructions, plan or document "
    "reviews, history or session reviews, broad questions), do NOT dump it. Give a short overview of the "
    "whole thing and offer more detail on any part. For step-by-step instructions give ONE step at a time "
    "and wait for the user to say next. For a broad or ambiguous question give a one-sentence summary answer "
    "and ask which part to narrow down to, or offer to go section by section. Ask exactly one question at a "
    "time inside a <raven> block, then end your turn and wait for the reply. Do not use the AskUserQuestion "
    "tool or plan mode; to plan, present the plan the same brief, sectioned way and ask whether to continue "
    "or go deeper. When you need approval for an action, use the normal permission flow."
)

PERSONA_PROMPT = (
    "Raven has switchable personas (character voices): {names}. If the user asks to switch, change, list "
    "or ask about personas, voices or characters (for example 'switch to the butler', 'use Raven', "
    "'what personas are there', 'change your voice'), they mean Raven's personas, NOT anything in the "
    "project. Use the mcp__raven__list_personas and mcp__raven__set_persona tools for those requests and "
    "do not search the project's files. The default persona is {default}. Note that 'Raven' is also the "
    "name of this voice assistant app and also the name of one persona: only treat it as the persona when "
    "the user clearly means the persona, and 'the default' or 'the normal one' means {default}. After "
    "switching, reply with one short <raven> line."
)

DISALLOWED_TOOLS = ["AskUserQuestion", "EnterPlanMode", "ExitPlanMode"]  # awkward by voice; prompt covers them


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


RAVEN_TOOLS = ("mcp__raven__list_personas", "mcp__raven__set_persona")


class ClaudeWorker:
    def __init__(self, cwd: str = ".", model: str | None = None, permission_mode: str | None = None,
                 cli_path: str | None = None, **_):
        import os

        self._cwd = os.path.abspath(os.path.expanduser(cwd))
        if not os.path.isdir(self._cwd):  # fail at startup, not after the user has already spoken
            raise FileNotFoundError(f"working directory does not exist: {self._cwd}")
        self._model, self._mode, self._cli_path = model, permission_mode, cli_path
        self.on_event = None
        self.controls = None
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True, name="claude-worker").start()
        self._client = None
        self._turn = 0
        self._task: asyncio.Task | None = None
        self._pending: asyncio.Future | None = None
        self.session_id: str | None = None   # learned from the first response; needed to open it in a terminal
        self.detached = False                # True while a terminal owns the session
        self._resume_next = False
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

    def session_info(self) -> dict | None:
        return ({"session_id": self.session_id, "cwd": self._cwd, "claude_path": self._cli_path}
                if self.session_id else None)

    def detach(self) -> None:
        """Hand the session to a human terminal: stop any running turn and release the session so two
        processes never write to it at once. attach() lets this worker resume it afterwards."""
        self._turn += 1  # drop any in-flight output
        self.detached = True
        asyncio.run_coroutine_threadsafe(self._release(), self._loop).result(timeout=20)

    def attach(self) -> None:
        self.detached = False  # the next send() reconnects with resume=<session_id>

    async def _release(self):
        await self._interrupt()
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._resume_next = self.session_id is not None

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
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

            prompt, extra = SESSION_PROMPT, {}
            if self.controls is not None:
                rows = self.controls.list_personas()
                default = next((p["name"] for p in rows if p.get("default")), rows[0]["name"])
                prompt += " " + PERSONA_PROMPT.format(names=", ".join(p["name"] for p in rows), default=default)
                # Not in allowed_tools: that would shadow can_use_tool (SDK warns); _decide allows them instead.
                extra = {"mcp_servers": {"raven": self._raven_server()}}
            opts = ClaudeAgentOptions(
                cwd=self._cwd,
                cli_path=self._cli_path,
                model=self._model,
                permission_mode=self._mode,
                can_use_tool=self._can_use_tool,
                env={"RAVEN": ""},
                system_prompt={"type": "preset", "preset": "claude_code", "append": prompt},
                disallowed_tools=DISALLOWED_TOOLS,
                hooks={"UserPromptSubmit": [HookMatcher(hooks=[self._persona_hook])]},
                resume=self.session_id if self._resume_next else None,
                **extra,
            )
            self._client = ClaudeSDKClient(options=opts)
            await self._client.connect()
        return self._client

    async def _persona_hook(self, input_data, tool_use_id, context):
        """UserPromptSubmit hook: attach who is speaking right now as context, on every turn, without touching
        the user's message. (Prefixing the message text put a paragraph of persona into every prompt, which
        showed up highlighted in the transcript when the session was opened by hand.)"""
        if self.controls is None:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                       "additionalContext": self.controls.persona_brief()}}

    def _raven_server(self):
        """In-process MCP server: the only way Claude touches the live Raven session."""
        from claude_agent_sdk import create_sdk_mcp_server, tool

        loop = asyncio.get_running_loop()

        def reply(text: str, error: bool = False) -> dict:
            out = {"content": [{"type": "text", "text": text}]}
            return {**out, "is_error": True} if error else out

        @tool("list_personas", "List Raven's personas (character voices) and which one is active.", {})
        async def list_personas(args):
            rows = await loop.run_in_executor(None, self.controls.list_personas)
            return reply("\n".join(
                f"{'* ' if r['active'] else '  '}{r['name']} (id: {r['id']})"
                + (" [default]" if r.get("default") else "") for r in rows))

        @tool("set_persona", "Switch Raven's live persona (voice and personality) by name, e.g. 'Charles'.",
              {"name": str})
        async def set_persona(args):
            try:
                return reply(await loop.run_in_executor(None, self.controls.set_persona, args["name"]))
            except ValueError as e:
                return reply(str(e), error=True)

        return create_sdk_mcp_server("raven", tools=[list_personas, set_persona])

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
        if self.detached:
            return self._emit(turn, "error", "the Claude session is open in a terminal; hand it back first")
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
                self.session_id = getattr(msg, "session_id", None) or self.session_id
                if isinstance(msg, AssistantMessage):
                    for b in msg.content:
                        if isinstance(b, TextBlock) and b.text.strip():
                            spoken, rest = split_raven(b.text)
                            for part in spoken:  # verbatim: no summarizing
                                self._emit(turn, "say", part)
                            if rest:
                                self._emit(turn, "text", rest)
                        elif isinstance(b, ToolUseBlock):
                            self._emit(turn, "tool", _tool_label(b.name, b.input))
                elif isinstance(msg, ResultMessage):
                    self._emit(turn, "error" if msg.is_error else "done", strip_tags(msg.result or ""))
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

        if name in RAVEN_TOOLS:  # the user's own session controls, already scoped to personas
            return PermissionResultAllow()
        if name in self._always:
            return PermissionResultAllow()
        turn = self._turn
        self._pending = fut = self._loop.create_future()
        self._emit(turn, "ask", _speakable(name, tool_input, ctx.title), ask_type="permission", tool=name,
                   detail=_tool_label(name, tool_input))
        decision, text = await fut
        if decision == "always":
            self._always.add(name)
        if decision in ("allow", "always"):
            return PermissionResultAllow()
        return PermissionResultDeny(message=text or "The user denied this action.")
