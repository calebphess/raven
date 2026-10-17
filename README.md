# Raven

Voice interface for Claude Code. Hold a key, talk, Claude works, then speaks back a short summary.

Speech to text: faster-whisper. Text to speech: ElevenLabs. Playback: `afplay` (macOS).

## Install

```
cp .env.example .env     # fill in ELEVENLABS_API_KEY etc.
pip install -e .
python install.py        # installs 4 hooks; python install.py --uninstall to remove
raven                    # starts Claude Code in voice mode
```

Needs ffmpeg, plus Microphone and Input Monitoring permission for your terminal.

## Use

- Hold **Right ⌥ Option**, talk, release. Your words are typed into the terminal.
- Raven speaks a plan before acting, asks when it needs permission, and gives a short synopsis at the end. Only text in `<raven>` tags is spoken.
- Permissions: Raven reads the request aloud. Hold the key and say yes, always, or no. No answer in 20 seconds shows the normal dialog.
- Voices: say "list voices" or "switch to Carter". Or run `raven-voice --list` / `raven-voice Carter`.

## How the pieces fit

- `launcher.py` sets `RAVEN=1`, starts the listener, and runs `claude` with `prompts/voice_mode.md` appended to its system prompt.
- Hooks (`prompt.py`, `speak.py`, `permission.py`) do nothing unless `RAVEN=1`, so plain `claude` sessions are untouched.
- The active persona lives only in `~/.raven_active_voice` and is injected every turn by `prompt.py`.
- `tts.py` is the single text-to-speech path.

## Troubleshooting

- Silence: check `ELEVENLABS_API_KEY` and that you started with `raven`, not `claude`.
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
