# Raven

A voice agent for Claude Code. You talk to a persona; a small local model answers instantly and speaks for itself, while Claude Code works in the background and hands back short, spoken answers.

- **Local and fast.** Speech-to-text (faster-whisper), the conversation model (llama.cpp) and the voices (Piper) all run on your machine. Claude Code is the only part that goes to the cloud.
- **Personas.** Dottie (default), Charles and Raven are built in; Carter and Mochi are downloadable packs. Each has its own voice, personality and pacing. Say "switch to Charles", or pick one in the app.
- **Claude does the work, in its own words.** Claude speaks through `<raven>` tags, so you hear its actual answer, kept brief and in the active persona's voice; the local model only acknowledges your request and reports progress.
- **Take over any time.** One click opens the live session in Terminal so you can drive it by hand, and hands it back afterwards.

## Quick start

```
pip install -e ".[v2,ui]"   # on an Intel Mac read the setup notes below first
raven                       # opens the app (needs Claude Code installed; the app checks)
```

Or build the double-clickable app: see [Raven.app](#ravenapp-macos).

| Command | What it does |
|---|---|
| `raven` | Open the Raven app |
| `raven chat` / `raven run` | Typed chat with spoken replies / push-to-talk in the terminal (Right ⌥ Option) |
| `raven --worker claude --cwd <dir> run` | Same, with Claude Code working in `<dir>` |
| `raven say "text" --persona charles` | Speak a line in a persona's voice |
| `raven personas list` / `use` / `install` / `remove` | Manage personas |
| `raven --legacy [claude args]` | The original V1 launcher (see below) |

`raven2` is the same command under its old name. Ideas that aren't built yet live in [FUTURE_CAPABILITIES.md](FUTURE_CAPABILITIES.md); the V2 design is in [docs/V2_PLAN.md](docs/V2_PLAN.md).

## Legacy (V1): `raven --legacy`

The first version of Raven is still here. It runs Claude Code in your terminal in voice mode: ElevenLabs voices, Whisper typing what you say into the session, and hooks that speak plans and summaries. Start it with `raven --legacy`; anything after the flag goes to `claude`, so `raven --legacy --resume` works. It shares nothing with V2 at runtime (V2 turns the V1 hooks off for its own Claude session).

### Install

```
cp .env.example .env     # fill in ELEVENLABS_API_KEY etc.
pip install -e ".[legacy]"
python install.py        # installs 4 hooks; python install.py --uninstall to remove
raven --legacy           # starts Claude Code in voice mode
```

Needs ffmpeg, plus Microphone and Input Monitoring permission for your terminal.

### Use

- Hold **Right ⌥ Option**, talk, release. Your words are typed into the terminal.
- Raven speaks a plan before acting, asks when it needs permission, and gives a short synopsis at the end. Only text in `<raven>` tags is spoken.
- Permissions: Raven reads the request aloud. Hold the key and say yes, always, or no. No answer in 20 seconds shows the normal dialog.
- Voices: say "list voices" or "switch to Carter". Or run `raven-voice --list` / `raven-voice Carter`.

### How the pieces fit

- `launcher.py` sets `RAVEN=1`, starts the listener, and runs `claude` with `prompts/voice_mode.md` appended to its system prompt.
- Hooks (`prompt.py`, `speak.py`, `permission.py`) do nothing unless `RAVEN=1`, so plain `claude` sessions are untouched.
- The active persona lives only in `~/.raven_active_voice` and is injected every turn by `prompt.py`.
- `tts.py` is the single text-to-speech path.

### Troubleshooting

- Silence: check `ELEVENLABS_API_KEY` and that you started with `raven --legacy`, not `claude`.
- Key does nothing: grant Input Monitoring to your terminal.
- Hooks missing: rerun `python install.py` with the same Python that has Raven installed.

## V2 setup notes (Intel Macs)

Things that bit us setting up `raven2` on an Intel MacBook (i5, no usable Metal GPU). Apple Silicon should not need most of this.

**Install order (in the project `.venv`)**

```
pip install --only-binary=:all: "av<13"        # av won't build from source here; install its wheel first
pip install --only-binary=:all: faster-whisper sounddevice huggingface_hub piper-tts
pip install "numpy<2"                           # onnxruntime 1.16 breaks on numpy 2
pip install -e .                                # then the patched llama-cpp-python below
```

**llama-cpp-python needs a patched build.** Its `CMakeLists.txt` forces Metal on for every Mac and turns AVX/AVX2/FMA off on Intel. Metal can't compile its shaders on an Intel GPU (`Failed to create llama_context`), and `CMAKE_ARGS=-DGGML_METAL=OFF` is ignored. Patch the sdist and install that:

```
mkdir -p ~/.raven/build && cd ~/.raven/build
pip download --no-deps --no-binary :all: llama-cpp-python && tar xzf llama_cpp_python-*.tar.gz
python3 - <<'PY'
import glob
p = glob.glob("llama_cpp_python-*/CMakeLists.txt")[0]
s = open(p).read()
for k in ("AVX", "AVX2", "FMA", "F16C"):  # i5-8279U and later Intel Macs support these
    s = s.replace(f'set(GGML_{k} "OFF" CACHE BOOL "ggml: enable {k}" FORCE)', f'set(GGML_{k} "ON" CACHE BOOL "ggml: enable {k}" FORCE)')
s = s.replace('set(GGML_METAL "ON" CACHE BOOL "ggml: enable Metal" FORCE)', 'set(GGML_METAL "OFF" CACHE BOOL "ggml: enable Metal" FORCE)')
s = s.replace('set(GGML_METAL_EMBED_LIBRARY "ON"', 'set(GGML_METAL_EMBED_LIBRARY "OFF"')
open(p, "w").write(s)
PY
pip install --force-reinstall --no-deps llama_cpp_python-*/
ls .venv/lib/python*/site-packages/llama_cpp/lib | grep -c metal   # should print 0
```

**Running**

- Start with `env -u RAVEN raven2 ...` so the V1 hooks stay quiet if `RAVEN` is set in your shell.
- Set `HF_HUB_OFFLINE=1` once models are downloaded. Otherwise faster-whisper can stall for minutes retrying the network.
- `raven2 --worker claude --cwd <real dir> run` (push-to-talk) or `chat` (typed). `--cwd` must exist.

**Building persona packs.** `python scripts/build_packs.py` writes `dist/packs/<id>.zip` and `index.json` (upload both to your pack host and set `RAVEN_PACKS_URL`). It needs each persona's Piper voice under `~/.raven/models`, downloaded from Hugging Face. On some networks `huggingface.co` times out during the TLS handshake while other sites work (we saw this until NordVPN was on); the app itself never needs Hugging Face at runtime. Downloads can also truncate silently: the build script loads and speaks each voice before zipping it, so a bad file is skipped rather than shipped.

## Raven.app (macOS)

Build a double-clickable app from the project venv (see the Intel notes above for installing the dependencies):

```
pip install -e ".[v2,ui,app]"
python scripts/build_app.py          # -> dist/Raven.app (ad-hoc signed, ~580 MB)
cp -R dist/Raven.app /Applications/  # optional
```

- **Needs:** [Claude Code](https://code.claude.com/docs/en/setup) installed (the app checks, and shows an install screen if it isn't), and the local models under `~/.raven/models` (the app lists what is missing; it does not download them yet).
- **Launch** goes straight to your home folder (no prompt). Use **File > New Project…** or **File > Open Project…** (also in the `⋯` menu) to point Claude at another folder; that starts a fresh session there without reloading the models. The first run asks macOS for Microphone and Input Monitoring access (needed for the push-to-talk key, Right ⌥ Option).
- **Icon:** `python scripts/make_icon.py <source.png>` regenerates `packaging/Raven.icns` and the in-app logo.
- **Debugging:** the app writes `~/.raven/logs/app.log` (startup stages, library diagnostics, crashes). `RAVEN_NO_PTT=1` skips push-to-talk setup; `RAVEN_SELFTEST="message"` sends one message, logs the result and quits.
- **Smoke test the built app safely:** `python scripts/smoke_app.py 60` (add `--missing-claude`, or `--selftest "message"`). It launches headless in a sandboxed Raven home with a Finder-like environment, caps the processes the app may create and kills its process group on timeout. Always test frozen apps this way: a frozen app that re-launches itself is a fork bomb.

