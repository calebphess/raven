# Raven review and simplification plan

Date: 2026-10-05. Reviewed against Claude Code 2.1.290 and the current hooks / CLI docs.

Goal of the product: Penn holds a key, talks, Claude does the work, and Claude talks back
only to (a) say what it is about to do, (b) ask permission, or (c) give a short readable
synopsis of what it did. Everything else stays on screen.

---

## Part 1: Why the persona keeps getting confused

There are **four separate places** that tell Claude how to speak, and they disagree.

| Source | Loaded when | What it says | Reliability |
|---|---|---|---|
| `~/.claude/CLAUDE.md` "Raven Voice Mode" section (written by `install.py`) | Every Claude Code session, voice or not | Use `<raven>` tags, address Penn, he/him, neutral tone. Says "when `RAVEN=1` is set", but the model cannot see env vars. | Always present |
| `launcher.py` `--append-system-prompt` | Only `raven` sessions | Same format rules again, identity again, plus a list of all voices and their personalities | Always present in voice sessions |
| `thinking.py` UserPromptSubmit hook `additionalContext` | Every session, but registered `"async": true` | The **active persona** ("Flirty… call Penn daddy") | **Unreliable.** Per the hooks docs, `additionalContext` from an async hook is not injected into the current turn. It arrives late or not at all. |
| `permission.py` | Every permission prompt, every session | Hardcoded "Your call, daddy." suffix regardless of active voice | Always, even when Carter or Charles is active |

Net effect: the neutral instructions arrive through two reliable channels, the persona
arrives through one unreliable channel, and the model also sees all three personalities
listed in the system prompt. It picks neutral most turns, flips to the persona some turns,
and sometimes blends. That is the confusion you are seeing.

Second-order problems that add to it:

- **Hooks are global.** `raven.speak`, `raven.thinking`, and `raven.permission` are in
  `~/.claude/settings.json` so they run in every Claude Code session. This typed session
  received the "Active voice persona: Raven" context too. Non-voice sessions get the persona
  and the `<raven>` instructions with no way to know they are not in voice mode.
- **Switching voices mid-session** changes `~/.raven_active_voice`, but earlier turns still
  carry the old persona text in context, and the system prompt is snapshotted for the whole
  conversation, so the model sees mixed signals.
- **Terminology drift.** `install.py` writes instructions for a `RAVEN:` … `END_RAVEN` line
  format. `speak.py` only parses `<raven>…</raven>` tags. `thinking.py` says "RAVEN: lines".
  The memory file says `RAVEN:` prefix. A fresh `python install.py` on a new machine would
  install instructions the parser cannot read, so nothing would be spoken.

### The fix in one sentence

One static channel for *format rules* (system prompt at launch), one reliable dynamic
channel for *persona* (a synchronous UserPromptSubmit hook), nothing in `CLAUDE.md`, and
every hook exits immediately unless it is inside a Raven session.

---

## Part 2: Bugs found (independent of the persona issue)

1. **`permission.py` output schema is wrong.** It prints `{"decision": "allow"}`. The
   documented PermissionRequest output is:
   ```json
   {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
     "decision": {"behavior": "allow"}}}
   ```
   `behavior` is `allow` or `deny` only. There is no `allow_always`. "Always" is done by
   adding `updatedPermissions` to the decision, and the hook **input** already contains
   `permission_suggestions`, which is a ready-made list to pass straight through.
2. **`_parse_decision` matches substrings.** `"all"` matches "call", "small", "allow";
   `"no"` matches "know", "now", "not sure"; `"go"` matches "good". And "no" is checked before
   "yes", so "yes, no problem" denies. Needs word-boundary matching and a sane order.
3. **`thinking.py` is async but returns `additionalContext`.** The async flag discards it.
   Either make the hook sync, or move the persona injection out of it.
4. **Whisper model loads per permission request.** `permission.py` constructs a new
   `WhisperModel` on every prompt (several seconds of dead air), then grabs the mic for a
   fixed 4 seconds while the listen daemon's stream is also open. Two processes own the mic
   and two Whisper models are resident.
5. **`speak.py` state file is global** (`/tmp/raven_speak_state.json`). Two concurrent
   Claude sessions overwrite each other's `last_line_index` and skip or repeat lines. Key the
   state by `session_id` (present in every hook input).
6. **`speak.py` runs on every PreToolUse in every session.** It spawns Python, imports
   the ElevenLabs SDK, and re-reads the whole transcript before each tool call. When a
   `<raven>` line exists it blocks the tool call until `afplay` finishes.
7. **`install.py` is stale.** Installs only the Stop hook (the other three were added by
   hand), writes the dead `RAVEN:`/`END_RAVEN` instructions, and picks whichever `python3`
   is first on PATH rather than the one that has the `raven` package installed.
8. **Four copies of `_speak`** (speak, permission, thinking, listen). Any TTS change has to
   be made four times.
9. **`.env.example` documents the 3-field voice format**; `config.py` parses a 4-field
   `Name:id:speed:personality` format. The default `ELEVENLABS_SPEED=1.3` in config is
   silently overridden by the per-voice speed when a voice is active.
10. **Docstrings lie.** `listen.py` says "macOS native speech recognition" (it is
    faster-whisper). `speak.py` says "RAVEN:-prefixed lines" (it is `<raven>` tags).
11. **No README.** The only install docs are in the memory file, which is also stale.

---

## Part 3: Target design

```
raven (launcher)
 ├─ sets RAVEN=1, RAVEN_SESSION_PROMPT built from: format rules + identity + active persona
 ├─ starts ListenDaemon (push-to-talk, Whisper, inject text)
 └─ exec claude --append-system-prompt-file <tmpfile> [user args]

Hooks (all gated: exit 0 immediately unless os.environ.get("RAVEN") == "1")
 ├─ UserPromptSubmit  raven.prompt      (SYNC) → additionalContext = active persona line
 │                                       also spawns detached TTS for a "thinking" phrase
 ├─ PreToolUse        raven.speak       → speaks new <raven> lines (plan before acting)
 ├─ Stop              raven.speak       → speaks new <raven> lines (synopsis)
 └─ PermissionRequest raven.permission  → speaks summary, asks daemon for a push-to-talk
                                           answer, returns documented decision JSON

raven/tts.py        single speak(text) used by everything; afplay detached by default
raven/voices.py     unchanged role: active-voice state file + command matching
raven/config.py     unchanged role; fix docs
```

Persona lives in exactly one place at runtime: the active-voice state file, read by the
sync UserPromptSubmit hook on every turn. The system prompt never names a persona, so
switching voices mid-session is clean. Nothing Raven-related is in `CLAUDE.md`.

---

## Part 4: Implementation steps (for the implementing model)

Work in this order. Each step is independently testable. Do not start the next step until
the verification for the current one passes.

### Step 1: Create `raven/tts.py` and remove the four duplicates

- New module with one public function:
  ```python
  def speak(text: str, *, block: bool = False) -> None
  ```
  Behavior: no-op if `ELEVENLABS_API_KEY` is empty or `text` is blank. Calls ElevenLabs
  `text_to_speech.convert` with `get_active_voice_id`, writes a temp mp3, then runs
  `afplay -r <get_active_speed()> <file>`. When `block=False` (default) use
  `subprocess.Popen(..., start_new_session=True)` and delete the file from a tiny background
  thread after the process exits (or leave deletion to a `finally` in a thread). When
  `block=True` use `subprocess.run`.
- Add a `__main__` so `python -m raven.tts "some text"` speaks it. The prompt hook uses this.
- Replace `_speak` in `speak.py`, `permission.py`, `thinking.py`, and `_speak_confirmation`
  in `listen.py` with calls to `tts.speak`. `permission.py` must call with `block=True`
  (it needs the alert to finish before listening).
- Verify: `python -m raven.tts "Hello Penn"` plays audio and exits.

### Step 2: Gate every hook on `RAVEN=1`

- In `speak.py`, `permission.py`, and the prompt hook, the first lines of `main()`:
  ```python
  if os.environ.get("RAVEN") != "1":
      sys.exit(0)
  ```
  The launcher already sets `RAVEN=1` in the env it passes to `claude`, and hooks inherit
  that env. This is the cheapest, most reliable gate and needs no settings changes.
- Verify: run a plain `claude` session (not via `raven`), send a prompt, confirm no
  thinking phrase is spoken and no persona context appears.

### Step 3: Make the persona channel synchronous (`raven/prompt.py`, replaces `thinking.py`)

- Rename `thinking.py` to `prompt.py`. Its `main()`:
  1. Gate on `RAVEN=1`.
  2. Fire-and-forget the thinking phrase:
     `subprocess.Popen([sys.executable, "-m", "raven.tts", phrase], start_new_session=True,
     stdout=DEVNULL, stderr=DEVNULL)`. Do not wait.
  3. Read `get_active_name()` and `get_active_personality()`. Print
     `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ...}}`
     with context text:
     > Raven voice persona for this turn: **{name}**. Everything inside `<raven>` tags must be
     > written in this voice: {personality}. This overrides any earlier persona.
  4. Exit 0. Total runtime should be well under 100 ms.
- In `~/.claude/settings.json`, change the UserPromptSubmit entry to
  `python3 -m raven.prompt` and **remove `"async": true`**. (Step 7 makes `install.py` do
  this; for now edit by hand to test.)
- Verify: in a `raven` session, say "switch to Carter", then ask a question. The reply's
  `<raven>` text should be in Carter's style on the very next turn. Say "switch to Raven"
  and confirm it flips back.

### Step 4: Rewrite the launcher system prompt and remove persona/voice lists from it

- Replace `_SYSTEM_PROMPT` in `launcher.py` with a file `raven/prompts/voice_mode.md`
  loaded at startup and passed with `--append-system-prompt-file`. Contents:

  ```
  # Raven voice mode

  You are running inside Raven, a voice interface for Claude Code. The user speaks to you
  by holding a key and talking; only text inside <raven>...</raven> tags is spoken back.

  ## When to speak
  Include exactly one <raven> block at each of these moments, and nowhere else:
  1. Before you start work: one or two sentences saying what you are about to do.
  2. When you need a decision or permission from the user.
  3. When you finish: a short synopsis of what you did and anything the user must know.
  If a turn is trivial (one quick answer), a single <raven> block with the answer is enough.

  ## What goes in a <raven> block
  - 1 to 3 plain sentences. Conversational, no markdown, no bullet lists, no code.
  - Summaries only. Never read file contents, command output, JSON, paths, URLs, or long
    lists aloud. Say "the config file exists and Raven is the active voice", not the JSON.
  - Say what changed and the key takeaway. Details stay on screen outside the tags.
  - Longer spoken pieces (a story, a poem) may use a multi-line <raven> block; newlines
    are collapsed to spaces.

  ## Persona
  Each turn includes a system reminder naming the active voice persona. Write the <raven>
  text in that persona's style. Text outside <raven> tags stays in your normal style.

  ## Voice commands that slip through
  Raven normally intercepts these, but speech recognition occasionally lets them through.
  If the user's message is just "list voices", "switch to <name>", "use <name>", or
  "change to <name>", run `raven-voice <name>` (or `raven-voice --list`) with Bash and
  confirm in one <raven> sentence. Do not treat these as coding requests.
  ```
  Append the identity lines (`Address the user as Penn. Use he/him pronouns.`) from `.env`
  at launch, as today.
- Add a tiny CLI `raven-voice` (`raven/voicecmd.py`, entry point in `pyproject.toml`):
  `raven-voice --list` prints names; `raven-voice <name>` calls `set_active_voice`. This
  replaces the instruction telling Claude to hand-write JSON to `~/.raven_active_voice`.
- Verify: `raven` starts, first turn produces a `<raven>` block before any tool call, and
  the voices list is no longer in the system prompt (`claude --debug` or just inspect the
  tmpfile contents).

### Step 5: Fix `permission.py`

- Gate on `RAVEN=1`.
- Output the documented schema:
  ```python
  out = {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                "decision": {"behavior": behavior}}}
  if always:
      out["hookSpecificOutput"]["decision"]["updatedPermissions"] = data.get("permission_suggestions", [])
  ```
  Only emit `updatedPermissions` when `permission_suggestions` is non-empty; otherwise a
  plain allow.
- Replace `_parse_decision` with word-boundary matching, checked in this order:
  deny phrases (`\bno\b`, `\bnope\b`, `\bdeny\b`, `\bstop\b`, `\bdon'?t\b`, `\bcancel\b`),
  then always phrases (`\balways\b`, `yes to all`, `allow all`, `all of them`),
  then allow phrases (`\byes\b`, `\byeah\b`, `\byep\b`, `\bsure\b`, `\bok(ay)?\b`,
  `\bgo ahead\b`, `\bdo it\b`, `\ballow\b`, `\bapprove\b`).
  Deny first is deliberate: a wrong deny just re-prompts; a wrong allow runs the command.
- Remove the hardcoded "daddy" suffix. Use a neutral suffix list ("Yes, always, or no?").
  Persona-flavored permission prompts can come later from a per-voice field if wanted.
- Replace fixed 4-second recording with **push-to-talk via the daemon** so the interaction
  is the same button everywhere:
  1. Hook writes `~/.raven/permission_pending` (a file containing the session_id), speaks the
     alert with `block=True`, then polls for `~/.raven/permission_answer` for up to 20 s.
  2. `listen.py`: after transcription, if `permission_pending` exists, write the transcript
     to `permission_answer`, delete `permission_pending`, and **do not inject** the text into
     the terminal.
  3. Hook reads the answer, deletes the file, parses the decision. If no answer within 20 s or
     unparseable, print nothing and exit 0 so the normal dialog appears.
  This removes the second Whisper model, the second mic stream, and the dead-air delay.
- Verify: trigger a Bash permission in a `raven` session. Hear the alert, hold Right Option,
  say "yes", release. Command runs. Repeat with "no" (denied) and "always" (no prompt on the
  next identical command). Say nothing: normal dialog appears after 20 s.

### Step 6: Tidy `speak.py`

- Gate on `RAVEN=1`.
- Key the state file by session: `~/.raven/speak_state_<session_id>.json`. Clean up files
  older than a day on each run (one `glob` + `unlink`, cheap).
- For the **Stop** event, prefer `data["last_assistant_message"]` when present and only fall
  back to transcript parsing when absent. PreToolUse still parses the transcript (there is
  no mid-turn text hook, confirmed in docs).
- Use `tts.speak(text, block=False)` for Stop (nothing to block). Keep `block=True` for
  PreToolUse only if you want the "plan" fully spoken before the tool runs; otherwise
  `block=False` lets the tool start while the plan is being read. Recommend `block=False`
  for both; the listen daemon already kills `afplay` when Penn starts talking.
- Fix the docstring.

### Step 7: Rewrite `install.py`

- Resolve the interpreter as `sys.executable` (the Python that is running the installer and
  therefore has `raven` installed), not `shutil.which("python3")`.
- Install/replace all four hooks idempotently (match on `"raven."` in the command, remove
  old entries, write new): UserPromptSubmit `raven.prompt` (sync), PreToolUse `raven.speak`,
  Stop `raven.speak`, PermissionRequest `raven.permission`. Keep any non-Raven hooks intact.
- **Remove** the "Raven Voice Mode" section from `~/.claude/CLAUDE.md` if present (find the
  `## Raven Voice Mode` heading, delete through the next `## ` heading or EOF). Do not add a
  new one. Voice-mode instructions now come only from the launcher.
- Add an `--uninstall` flag that removes the hooks and the CLAUDE.md section.
- Verify: `python install.py` twice in a row is idempotent; `~/.claude/CLAUDE.md` has no
  Raven section; `~/.claude/settings.json` has exactly four `raven.` hook entries.

### Step 8: Config and docs

- `.env.example`: document the 4-field voice format `Name:voiceId:speed:personality` and
  state that per-voice speed overrides `ELEVENLABS_SPEED`.
- Fix the `listen.py` docstring (faster-whisper, not macOS speech).
- Write a short `README.md`: what it does, install steps, the key, voice commands, how
  permission by voice works, troubleshooting (mic / Input Monitoring permissions, ffmpeg).
- Update `~/.claude/projects/-Users-penn-Development-raven/memory/MEMORY.md`: `<raven>`
  tags not `RAVEN:` prefix; four hooks not one; hooks gated on `RAVEN=1`.

### Optional later improvements (not part of this pass)

- Use ElevenLabs' native `voice_settings={"speed": ...}` (range 0.7 to 1.2) instead of
  `afplay -r`, which resamples and can degrade quality at 1.3x.
- Scope hooks to Raven sessions via `claude --settings '<json>'` instead of the env gate, so
  non-Raven sessions never even spawn the hook processes. Verify first that hooks from
  `--settings` merge with, rather than replace, the `hooks` key from `settings.json`.
- Per-voice permission suffixes (so Charles says "Shall I proceed, sir?").
- Streaming TTS (ElevenLabs `convert_as_stream` piped to `afplay`) to cut first-audio latency.

---

## Part 5: Acceptance test for the whole pass

1. Start `raven`. Hold Right Option, say "what files are in this project". Hear a short
   plan or answer in the Raven persona. Nothing but `<raven>` text is spoken.
2. Say "switch to Charles". Hear "Switched to Charles." Ask another question; the spoken
   reply is in butler style on that same next turn.
3. Ask for something that needs permission (for example "run the test suite"). Hear the
   alert, hold the key, say "yes". It runs. Spoken synopsis at the end is one to three
   sentences and does not recite output.
4. Quit. Start plain `claude` in another directory. Send a prompt. Nothing is spoken, no
   persona reminder appears, no `<raven>` tags in the reply.
5. `grep -n "Raven" ~/.claude/CLAUDE.md` returns nothing.
