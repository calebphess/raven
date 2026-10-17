# Future capabilities

Not started. Ideas and planned work for Raven V2.

- **Install personas by voice via Claude**: a third Raven tool (`install_persona`) so "get me the cowboy" / "install Mochi" downloads the pack from the pack host. Claude should also be told which packs are *available* (host `index.json`), not just installed.
- **Separate display text from spoken text**: keep what prints in the terminal (`$183.03`, `3.5%`, `v2`, `src/core.py`) distinct from what goes to the TTS, with a phonetic "speakable" pass in between: numbers to words ("one hundred and eighty-three dollars and three cents"), percents, dates and times, ordinals, units, abbreviations/acronyms, symbols, version numbers, and paths/URLs reduced to something sayable. Needs to run per speech chunk after the sentence chunker (so numbers are never split), be locale-aware, and be pluggable per persona/voice (e.g. a TTS-specific lexicon for names the voice mispronounces). The log keeps the original text.
- Bigger default LLM (3B) for better intros, acks and readouts; `apple-silicon` profile already points at it.
- Streaming progress readouts while Claude works (currently silent until it finishes).
- Menubar app + pop-up window, optional hidden "Claude Code shell" view; macOS notification/sound when Claude finishes while the terminal is in the background.
- Remote worker (Claude on a home machine) and Android shell.
- Make `--worker claude` the default; remove V1 hooks/installer once V2 is the daily driver.
