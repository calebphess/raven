# Future capabilities

Not started. Ideas and planned work for Raven V2.

- **Install personas by voice via Claude**: a third Raven tool (`install_persona`) so "get me the cowboy" / "install Mochi" downloads the pack from the pack host. Claude should also be told which packs are *available* (host `index.json`), not just installed.
- **Separate display text from spoken text**: keep what prints in the terminal (`$183.03`, `3.5%`, `v2`, `src/core.py`) distinct from what goes to the TTS, with a phonetic "speakable" pass in between: numbers to words ("one hundred and eighty-three dollars and three cents"), percents, dates and times, ordinals, units, abbreviations/acronyms, symbols, version numbers, and paths/URLs reduced to something sayable. Needs to run per speech chunk after the sentence chunker (so numbers are never split), be locale-aware, and be pluggable per persona/voice (e.g. a TTS-specific lexicon for names the voice mispronounces). The log keeps the original text.
- Bigger default LLM (3B) for better intros, acks and readouts; `apple-silicon` profile already points at it.
- UI polish: a built-in (embedded) Claude Code shell view instead of opening Terminal; menubar icon that reflects worker status; macOS notification/sound when Claude finishes while the window is hidden; per-app terminal choice beyond Terminal/iTerm; package as a double-clickable .app.
- Reading shell commands aloud sensibly (Claude currently spells symbols: "python three space dash m space venv"); part of the speakable-text pass above.
- Remote worker (Claude on a home machine) and Android shell.
- Make `--worker claude` the default; remove V1 hooks/installer once V2 is the daily driver.
- **First-run model download inside the app**: Raven.app does not bundle the models (language model ~0.8 GB, speech model ~0.15 GB, voices ~60 MB each); on launch it checks for them and says what is missing. It should download them itself with a progress screen, from a host you control (Hugging Face is blocked on some networks).
- **Distributable app**: sign with a Developer ID and notarize so it opens on other Macs without the Gatekeeper warning (it is ad-hoc signed today, fine on the Mac it was built on); universal2/Apple Silicon build; a DMG.
- **Smaller bundle (581 MB)**: scipy is ~115 MB and is used only for audio resampling; replace it with a small numpy resampler.
- **Menubar icon as a proper macOS template image** (monochrome, adapts to light/dark menu bars), and reflecting status.
- **Remember the last project / Open Recent**: Raven always starts in the home folder today; File > Open Recent and an option to reopen the last project would help people who live in one repo.
- **Voice commands for projects** ("open the raven project", "new project called x") alongside the File menu.

