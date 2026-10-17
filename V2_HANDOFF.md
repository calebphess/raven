# Raven V2 handoff (pick up in plain Claude Code)

Branch: `v2` (created locally from `main`, nothing committed on it yet). Full approved plan: `docs/V2_PLAN.md`.

## What V2 is
Standalone, local-first voice agent. A small local LLM is the conversational "front brain"; STT and TTS run offline; Claude Code runs hidden in the background as a worker. Every user message interrupts and goes straight to Claude. Raven gives a super brief spoken readout whenever Claude returns something, and relays approvals/questions. Personas (name + voice pack + personality) are local and switchable. UI: menubar app + pop-up window, global hotkey works with the window closed, optional hidden "Claude Code shell" view.

## Requirements the user added along the way
- Portability: must work on newer Macs (Apple Silicon) and Android without gutting the app. Models/backends are swappable via profiles (`intel-cpu`, `apple-silicon`, `android`) and plugin interfaces (STT / LLM / TTS / Worker).
- Remote: Claude Code can run on a home machine (`RemoteWorker` + small host agent, encrypted link via Tailscale/SSH, token pairing). Same event protocol as local.
- Isolation: no persona/voice context reaches Claude. Start the worker with `RAVEN` unset so the V1 hooks no-op. Each V2 session gets a short appended system prompt saying it runs behind Raven, the user talks to Raven, output gets summarized aloud, keep updates brief with the result first, ask one direct question for approvals, no special tags, no personality text.
- Public repo (`github.com/calebphess/raven`, created under the `calebphess` gh account): never commit `.env` or personal data.

## Environment facts
- Dev machine: Intel i5 MacBook, 16 GB, no Metal GPU. Python 3.10.10. Claude Code 2.1.302 at `~/.local/bin/claude`.
- Already installed globally: `faster-whisper 1.2.1`, `onnxruntime 1.16.3`, `sounddevice`.
- `.venv/` created in the repo (gitignored) with `claude-agent-sdk` installed.
- `timeout` is not available on this Mac's shell; use Python-side timeouts.

## Spike results so far (`spikes/spike_sdk.py`, usage: `python spikes/spike_sdk.py interrupt|queue <scratch cwd>`)
- **interrupt() then query():** WORKS. A running `sleep 20` Bash task was cancelled immediately and the new message was answered about 3 s later. Plan: always do interrupt-then-send.
- **query() with no interrupt:** HUNG with no output for several minutes (process killed). Not confirmed why; likely the SDK doesn't accept a mid-run message on its own. Rerun only if curious, now with a 75 s `asyncio.wait_for` already in the script. `env -u RAVEN` when running it.
- **Leak found:** the hidden Claude session replied with `<raven>` tags and a flirty persona because the V1 hooks fired (they fire when `RAVEN=1` is inherited). Confirms the isolation requirement above.

## Latency spike results (`spikes/spike_latency.py`, intel-cpu: i5-8279U, 8 threads)
Run: `HF_HUB_OFFLINE=1 env -u RAVEN .venv/bin/python -u spikes/spike_latency.py base`
- STT faster-whisper base int8, 3 s clip: 0.65-1.3 s (noisy; `tiny` not measured, download timed out).
- LLM Llama-3.2-1B Q4_K_M: load 2.8 s, first token ~65 ms warm, ~13 tok/s, first full sentence ~1.5 s.
- TTS Piper lessac-low: first audio ~160 ms.
- Sum to first audio is ~2.4-3 s, over the 800 ms target. Levers: stream LLM output at clause boundaries (~0.5 s), `tiny` STT, spoken instant acknowledgement while the brain works, VAD endpointing so STT overlaps speech.

## Build gotchas (venv)
- `av` has no source build here: `pip install --only-binary=:all: "av<13"` first, then faster-whisper.
- Pin `numpy<2` (onnxruntime 1.16).
- llama-cpp-python's sdist CMakeLists forces Metal ON and AVX/AVX2/FMA OFF on every Mac; Metal fails on this Intel GPU ("Failed to create llama_context"). CMAKE_ARGS is ignored. Fix: patched copy at `~/.raven/build/llama_cpp_python-*/` (Metal OFF, AVX/AVX2/FMA/F16C ON) installed with `pip install --force-reinstall --no-deps <dir>`. The `intel-cpu` install step needs to automate this.
- Models in `~/.raven/models/`. Set `HF_HUB_OFFLINE=1` or faster-whisper stalls on network retries.

## State of the system
- V1 hooks are still installed in `~/.claude/settings.json` (4 entries: `raven.prompt`, `raven.speak` x2, `raven.permission`). Remove later with `python install.py --uninstall` (backs up settings) once V2 replaces V1. `~/.claude/CLAUDE.md` has no Raven section.
- `main` is pushed to GitHub with one commit. `v2` branch, `docs/`, `spikes/`, `.venv/`, and this file are local and uncommitted.

## Next steps (from the plan)
1. Spike mostly done (see results). Remaining: measure `tiny` STT, Kokoro vs Piper quality, clause-level streaming; then fix the `intel-cpu` profile.
2. Build the headless core loop (capture -> STT -> brain -> TTS with barge-in), reusing `raven/listen.py` capture + transcribe, `raven/config.py` `VoiceEntry`, `raven/voices.py` matchers.
3. Add the Claude worker (Agent SDK, `can_use_tool` approvals, summarizer readouts), then personas/voice packs, then the menubar UI, then remote host agent, then hardening and V1 removal.

## V2 core landed (package `raven2/`, tests in `tests/test_v2.py`, 18 passing)
- Ports in `raven2/interfaces.py` (STT, LLM, TTS, Worker, AudioOut); backends named in profiles and loaded lazily via `raven2/registry.py`. Only `raven2/app.py` knows concrete classes. Swap = edit `~/.raven/config.toml` or `registry.register(...)`.
- Personas: built-ins `dottie` (default), `charles`, `raven` in `raven2/personas_builtin/`; downloadable packs in `packs/` (`carter` cowboy, `mochi` kawaii). Add one: `raven2 personas install <dir|.toml|.zip|url|pack-name>`. Voice effects (pitch, ring mod, comb) live in the persona TOML.
- Voice commands: "list personas", "switch to charles", "be raven" (`raven2/commands.py`); CLI: `raven2 personas list|use|install|remove`, `raven2 chat`, `raven2 run` (push-to-talk), `raven2 say`.
- Voices are Piper models fetched lazily from Hugging Face on first use. HF was unreachable from this machine when built, so only `lessac-low` is local; tune each persona's sound by ear once downloaded.
- Not done: Claude worker (NullWorker placeholder), UI, remote worker, installer for the patched llama-cpp-python.

## Packs + Claude worker landed
- Packs: `python scripts/build_packs.py` -> `dist/packs/<id>.zip` + `index.json` (sha256, verified on install; validates each voice loads before zipping). Host base via `RAVEN_PACKS_URL` / `packs_url` in config.toml; defaults to repo `dist/packs` in a dev checkout. Runtime never touches Hugging Face. NOTE: this network blocks huggingface.co unless NordVPN is on (building packs needs it).
- Worker: `raven2 --worker claude [--cwd DIR] chat|run`. `raven2/backends/claude_worker.py` (Agent SDK, `RAVEN` blanked, appended session prompt, new send interrupts, approvals via `can_use_tool` fail closed, never auto-allow). Verified against real Claude: file creation, approval ask/allow, mid-run interrupt (`spikes/spike_worker.py`).
- Front end: instant persona `acks` on hand-off, voiced approvals ("Claude wants to create x. Yes, always, or no?"), spoken yes/no routed back as the answer, 1-sentence persona readout when Claude finishes (`Brain.relay`). 26 tests.
- Known gaps: readout quality is limited by the 1B model (try 3B); AskUserQuestion answers return to Claude as deny-with-message text; no UI, no remote worker, no streaming progress updates, no V1 removal yet; `--worker claude` isn't in the default profile.

## Persona control + status line (landed)
- Claude gets `list_personas` / `set_persona` tools (in-process MCP, `RavenControls` interface) and a session prompt that says persona/voice requests mean Raven's personas. A switch makes the new persona announce itself (model-generated intro) and skips the readout.
- Status line (`raven2/status.py`): pinned bottom line + tab title in `run`/`chat`: `○ ready`, `⠹ Claude working 12s · 3 tools`, `? Claude is waiting for your answer`, `✓ Claude finished in 20s`, `✗ Claude error`, plus a mic overlay (`● listening…`, `◌ transcribing…`). Auto-disabled when not a TTY.
- Speech chunker never splits numbers (`$186.03`, `1,000`): a boundary is punctuation followed by whitespace.

## Future capabilities (not started)
- **Install personas by voice via Claude**: a third Raven tool (`install_persona`) so "get me the cowboy" / "install Mochi" downloads the pack from the pack host. Claude should also be told which packs are *available* (host `index.json`), not just installed.
- **Separate display text from spoken text**: keep what prints in the terminal (`$183.03`, `3.5%`, `v2`, `src/core.py`) distinct from what goes to the TTS, with a phonetic "speakable" pass in between: numbers to words ("one hundred and eighty-three dollars and three cents"), percents, dates and times, ordinals, units, abbreviations/acronyms, symbols, version numbers, and paths/URLs reduced to something sayable. Needs to run per speech chunk after the sentence chunker (so numbers are never split), be locale-aware, and be pluggable per persona/voice (e.g. a TTS-specific lexicon for names the voice mispronounces). The log keeps the original text.
- Bigger default LLM (3B) for better intros, acks and readouts; `apple-silicon` profile already points at it.
- Streaming progress readouts while Claude works (currently silent until it finishes).
- Menubar app + pop-up window, optional hidden "Claude Code shell" view; macOS notification/sound when Claude finishes while the terminal is in the background.
- Remote worker (Claude on a home machine) and Android shell.
- Make `--worker claude` the default; remove V1 hooks/installer once V2 is the daily driver.
