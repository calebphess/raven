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
