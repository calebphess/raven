# Raven V2: standalone, local-first voice agent with Claude Code as a hidden worker

## Context
V1 is a set of hooks inside a visible Claude Code terminal. Latency is bound to the cloud model and ElevenLabs, persona is injected through four conflicting channels (see PLAN.md), and Claude has to format its own output for speech. V2 inverts this: **Raven is the app**. A small local LLM is the conversational front brain, STT and TTS run offline, and Claude Code is a background worker the user never sees. Raven relays what it is doing in brief spoken readouts and asks for approvals on Claude's behalf.

Work happens on a `v2` branch of `calebphess/raven` (public repo; no secrets or personal data committed).

## Architecture
```
hotkey/mic -> VAD -> local STT (Whisper) -> Front Brain (local LLM, persona prompt)
                                               |  always forwards user turn to Claude (interrupting)
                                               v
                                   Claude Worker (Agent SDK, hidden)
                                               | events: text / tool_use / permission / result
                                               v
                                   Event Summarizer (front brain) -> TTS (local, persona voice)
UI shell (menubar + pop-up window): transcript, text input, status. Hotkey works with window closed.
```

Rules from the user:
- Every user utterance or typed message is **always sent to Claude immediately**, interrupting any running task (like sending mid-run in Claude Code).
- The front brain stays conversational, passes context along, and speaks a **super brief readout every time Claude returns something**.
- Permission and question requests from Claude are voiced by Raven; Raven relays the user's answer.
- Personas = voice pack + personality text + name, all local and switchable; fully offline; fast.
- Claude keeps full local capability (files, web, APIs, logged-in accounts) because it is the real local Claude Code.
- Core must stay portable: STT / LLM / TTS / Worker are interfaces so mobile-capable backends (whisper.cpp, llama.cpp, ONNX TTS) can be swapped in later.

## Component decisions (my recommendations)
| Part | Choice | Why |
|---|---|---|
| STT | `faster-whisper` now, behind `STT` interface; add `vad_filter` / streaming VAD (Silero) | Reuse V1 core; whisper.cpp is the mobile path |
| Front LLM | `llama.cpp` (`llama-cpp-python`), GGUF ~1-3B instruct (Qwen2.5/3 or Llama 3.2 3B, Metal) behind `LLM` interface | Small, fast, runs on phones, OpenAI-compatible swap-in possible |
| TTS | Kokoro (ONNX, 82M) as default backend; Piper as a second backend; `TTS` interface | Offline, fast, many voices, mobile-viable |
| Claude link | `claude-agent-sdk` (Python) using the user's installed/logged-in Claude Code | Streaming events, `can_use_tool`, `interrupt()`, sessions; full CLI capability |
| UI | Menubar app + pop-up window (PySide6 tray + chat window), global hotkey via `pynput` | Matches requested UX in one language; core is UI-agnostic |

## Hardware portability (requirement added by user)
Dev machine is an **Intel i5 Mac, 16 GB, no Metal-class GPU**, so the default profile must be CPU-light; Apple Silicon Macs and Android phones must work without gutting the app.
- **Model registry + profiles**: no model is hardcoded. `~/.raven/config.toml` selects a *profile* (`intel-cpu`, `apple-silicon`, `android`, or custom) that names, per slot (STT / LLM / TTS), a backend and a model id/path plus params (threads, quantization, device).
- **Backends are plugins** behind the `STT` / `LLM` / `TTS` / `Worker` interfaces, discovered by name (entry points). Swapping models = editing the profile or `raven models set llm <id>`; adding a backend = one new class.
- **Profiles**: `intel-cpu` -> faster-whisper tiny/base int8, ~1B Q4 GGUF LLM, Piper TTS. `apple-silicon` -> larger Whisper/LLM via Metal or MLX, Kokoro. `android` -> whisper.cpp, llama.cpp (GGUF), ONNX Piper/Kokoro.
- **Android reality**: the Python core will not run natively on a phone. The portable contract is the interfaces, the event protocol, the persona manifest and the profile format; the Android app is a thin native shell that reimplements the same loop with the same GGUF/ONNX models and talks to Claude Code on the desktop over a local network link.
- The spike's latency harness runs per profile, so each hardware class gets measured numbers.

## Remote worker support (requirement added by user)
Raven (voice, front brain, UI) can run on a laptop or phone while Claude Code runs on a **home machine**.
- `Worker` interface gets two implementations: `LocalWorker` (Agent SDK in-process) and `RemoteWorker` (talks to a small **Raven host agent** running next to Claude Code on the home machine).
- Host agent: wraps the same Agent SDK session and exposes the event protocol (send / interrupt / events / permission request+reply) over an authenticated, encrypted link. Default transport: WebSocket over Tailscale/WireGuard or SSH tunnel; no open public ports; pairing token on first connect.
- Same event protocol locally and remotely, so the front brain, summarizer and approvals code do not change. The Android shell uses `RemoteWorker` by default.
- Config: `worker = "local" | "remote"` plus host address and token in `~/.raven/config.toml`.
- The user's last phrase ("a hot food shell") was unclear in transcription; I read it as a separate host/shell component and need to confirm what was meant.

## Claude Code shell (requirement added by user)
Besides the Raven conversation, the UI offers an optional **Claude Code shell** view: a raw, read/write window onto the hidden session (transcript, tool calls, manual input) for when the user wants to see or drive Claude directly. Works for local and remote workers via the same event protocol. Hidden by default.

## Worker isolation and session prompt (requirement added by user)
- Persona/voice context must never reach Claude. Today it leaks in through the four V1 hooks in `~/.claude/settings.json` (`raven.prompt`, `raven.speak` x2, `raven.permission`), all gated on `RAVEN=1`. (`~/.claude/CLAUDE.md` has no Raven section; verified.)
- V2 worker launches Claude with `RAVEN` removed from its env so those hooks no-op, and keeps the user's other settings, MCP servers and logins intact (full Claude Code capability).
- Each new V2 session starts with an appended system prompt, roughly: *you are running behind Raven, a voice assistant front-end; the user talks to Raven, which relays their messages and summarizes your output aloud; keep updates brief and put the key result up front; when you need a decision or approval, ask one direct question; do not use any special tags.* No persona or personality text.
- Cleanup of the V1 hooks (`python install.py --uninstall`, which backs up settings first) is **deferred until V2 replaces V1**, because this current session's voice still depends on them.

## Modules (new package `raven2/` on the v2 branch; V1 removed once parity is reached)
- `core/events.py`: async event bus (user_text, stt_partial, claude_event, speak, interrupt, status).
- `audio/capture.py`: from `raven/listen.py` (InputStream, 16 kHz, min-length 0.3 s gate, Right Option); drop clipboard/osascript injection.
- `stt/whisper.py`: lazy model load (not at import), language configurable.
- `llm/local.py`: streaming generate with cancel; prompt assembly.
- `brain/front.py`: persona system prompt (from `raven/prompts/voice_mode.md` rules: summaries only, never read paths/code), conversation memory, decides spoken replies; `brain/summarizer.py` turns Claude events into 1-sentence readouts and groups bursts of tool events.
- `worker/claude.py`: owns a `ClaudeSDKClient`; `send(text)` = `interrupt()` then `query()`; maps SDK messages to events; `can_use_tool` callback routes approvals to the brain.
- `brain/approvals.py`: reuse `build_speech` and `parse_decision` ideas from `raven/permission.py`; allow / always / deny, with timeout falling back to asking again (never auto-allow).
- `tts/` : `base.py`, `kokoro.py`, `piper.py`; in-process audio queue with immediate `cancel()` (replaces the fcntl lock + `pkill afplay`); barge-in when the user starts talking.
- `personas/`: manifest (`name`, `voice_pack`, `speed`, `personality`, optional `wake names`) in `~/.raven/personas/*.toml`; reuse `VoiceEntry` shape from `raven/config.py` and the `match_voice_command` / `match_list_command` regexes from `raven/voices.py` as a zero-latency fast path; `raven personas install <voice-pack>` downloads packs.
- `ui/`: tray icon, chat window (transcript, text box, status/persona), settings for hotkey, persona, model paths.
- `config.py`: from `raven/config.py`; no API keys needed by default (ElevenLabs optional later as a pluggable TTS backend). Env > `~/.raven/config.toml`.

## Phases
1. **Spike (de-risk first)**: (a) Agent SDK interrupt-and-resend semantics and mid-run message behavior; (b) end-to-end latency of local STT -> LLM first token -> TTS first audio on this Mac; (c) choose model size from measurements. Target: first audio < ~800 ms for conversational replies.
2. **Core loop headless (CLI)**: audio capture -> STT -> brain -> TTS, with barge-in. No Claude yet.
3. **Claude worker**: SDK session, always-interrupt send, event stream, summarizer readouts, approvals by voice.
4. **Personas + voice packs**: manifest, install/switch by voice and UI, per-persona prompt.
5. **Menubar + window UI**; background hotkey; typed input path shares the same send.
6. **Hardening**: tests (see below), README rewrite, remove V1 hooks/installer (keep a one-time cleanup of old `~/.claude/settings.json` hook entries), packaging.

## Critical files to reuse / replace
- Reuse: `raven/listen.py` (record + transcribe core), `raven/config.py` (`VoiceEntry`, loader), `raven/voices.py` (command matchers), `raven/permission.py` (`build_speech`, `parse_decision`), `raven/prompts/voice_mode.md` (rules).
- Replace: `raven/tts.py` (ElevenLabs/afplay), `raven/speak.py` (transcript tailing, `<raven>` tags), `raven/prompt.py`, `raven/launcher.py`, `install.py`, `raven-voice` CLI.

## Verification
- Unit tests (none exist in V1): persona manifest parsing, command matchers, `parse_decision`, summarizer grouping, event-bus ordering.
- Worker integration test: fake SDK stream -> expected spoken readouts; real run: ask Claude to edit a file in a scratch dir and confirm readouts, an approval prompt, and an interrupt mid-task.
- Latency harness logging per-stage timings (STT, LLM first token, TTS first audio).
- Manual: offline test with network disabled for conversational turns; hotkey works with window closed; persona switch changes voice and tone; mic and Input Monitoring permissions.

## Open items to settle during the spike
- Whether the SDK accepts a new message mid-run without `interrupt()` (affects "send now" behavior).
- Final LLM and TTS model sizes after measuring latency and quality.
- Whether to later split the UI into a Tauri/Swift shell with the Python core as a sidecar (kept possible by the event-bus boundary).
