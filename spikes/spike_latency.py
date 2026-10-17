"""Per-stage latency harness for the intel-cpu profile.

Usage: python spikes/spike_latency.py [whisper_model] [threads]
Stages: STT (faster-whisper int8) -> LLM first token / first sentence (llama.cpp) -> TTS first audio (Piper).
"""
import os
import sys
import time

import numpy as np

MODELS = os.path.expanduser("~/.raven/models")
WHISPER = sys.argv[1] if len(sys.argv) > 1 else "base"
THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else os.cpu_count() or 4
LLM_PATH = os.path.join(MODELS, "Llama-3.2-1B-Instruct-Q4_K_M.gguf")
PIPER_PATH = os.path.join(MODELS, "en/en_US/lessac/low/en_US-lessac-low.onnx")
SENTENCE_END = (".", "!", "?")


def ms(t0):
    return (time.perf_counter() - t0) * 1000


def stt(piper_voice):
    from faster_whisper import WhisperModel

    t = time.perf_counter()
    model = WhisperModel(WHISPER, device="cpu", compute_type="int8", cpu_threads=THREADS)
    print(f"[stt] load {WHISPER}: {ms(t):.0f} ms")

    # Synthesize a ~3 s test utterance with Piper so the STT input is real speech.
    chunks = [c.audio_int16_array for c in piper_voice.synthesize("Can you check the tests in the project and tell me what failed")]
    pcm = np.concatenate(chunks).astype(np.float32) / 32768.0
    sr = piper_voice.config.sample_rate
    if sr != 16000:
        idx = np.linspace(0, len(pcm) - 1, int(len(pcm) * 16000 / sr))
        pcm = np.interp(idx, np.arange(len(pcm)), pcm).astype(np.float32)
    print(f"[stt] test clip: {len(pcm) / 16000:.1f} s")

    for i in range(3):
        t = time.perf_counter()
        segs, _ = model.transcribe(pcm, language="en", beam_size=1, vad_filter=False)
        text = " ".join(s.text for s in segs).strip()
        print(f"[stt] run {i}: {ms(t):.0f} ms -> {text!r}")
    return text


def llm(prompt):
    from llama_cpp import Llama

    t = time.perf_counter()
    model = Llama(model_path=LLM_PATH, n_ctx=2048, n_threads=THREADS, n_gpu_layers=0, verbose=True)
    print(f"[llm] load: {ms(t):.0f} ms")
    messages = [
        {"role": "system", "content": "You are Raven, a brief voice assistant. Reply in one or two short sentences."},
        {"role": "user", "content": prompt},
    ]
    sentence = ""
    for i in range(3):
        t = time.perf_counter()
        first_tok = first_sent = None
        n = 0
        out = ""
        for chunk in model.create_chat_completion(messages=messages, max_tokens=80, stream=True):
            piece = chunk["choices"][0]["delta"].get("content", "")
            if not piece:
                continue
            n += 1
            out += piece
            if first_tok is None:
                first_tok = ms(t)
            if first_sent is None and out.rstrip().endswith(SENTENCE_END):
                first_sent = ms(t)
                sentence = out.strip()
        total = ms(t)
        print(f"[llm] run {i}: first token {first_tok:.0f} ms, first sentence {first_sent or total:.0f} ms, {n} tok in {total:.0f} ms")
    return sentence or out.strip()


def tts(voice, text):
    for i in range(3):
        t = time.perf_counter()
        first = None
        total_samples = 0
        for chunk in voice.synthesize(text):
            if first is None:
                first = ms(t)
            total_samples += len(chunk.audio_int16_array)
        audio_s = total_samples / voice.config.sample_rate
        print(f"[tts] run {i}: first audio {first:.0f} ms, total {ms(t):.0f} ms for {audio_s:.1f} s of audio")


def main():
    from piper import PiperVoice

    voice = PiperVoice.load(PIPER_PATH)
    heard = stt(voice)
    reply = llm(heard)
    print(f"[llm] reply: {reply!r}")
    tts(voice, reply)


if __name__ == "__main__":
    main()
