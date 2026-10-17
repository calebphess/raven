import numpy as np
import pytest

from raven2 import commands, personas as P, registry
from raven2.brain import Brain, clauses
from raven2.core import Assistant, Services
from raven2.interfaces import Audio


def test_builtins_load():
    allp = P.load_all(personas_dir=None)
    assert {"dottie", "charles", "raven"} <= set(allp)
    assert allp["charles"].voice.model == "en_GB-alan-medium"
    assert allp["raven"].voice.effects["pitch"] == -1.5


def test_install_pack_and_override(tmp_path):
    p = P.install(str(__import__("pathlib").Path(__file__).parent.parent / "packs" / "carter"), tmp_path)
    assert p.id == "carter" and (tmp_path / "carter" / "persona.toml").exists()
    allp = P.load_all(tmp_path)
    assert allp["carter"].source == "installed"
    assert P.remove("carter", tmp_path) and "carter" not in P.load_all(tmp_path)


def test_install_rejects_bad_pack(tmp_path):
    f = tmp_path / "bad.toml"
    f.write_text('name = "x"\n')  # no [voice]
    with pytest.raises(ValueError):
        P.install(str(f), tmp_path / "out")
    assert not (tmp_path / "out" / "bad").exists()


def test_zip_slip_blocked(tmp_path):
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("persona.toml", '[voice]\nmodel="en_US-joe-medium"\n')
        z.writestr("../evil.txt", "x")
    zp = tmp_path / "evil.zip"
    zp.write_bytes(buf.getvalue())
    with pytest.raises(ValueError):
        P.install(str(zp), tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.parametrize("text", ["list personas", "What personas are there?", "show voices",
                                  "who can I talk to", "Which characters are available"])
def test_list_commands(text):
    assert commands.match(text, P.load_all()).kind == "list"


@pytest.mark.parametrize("text,pid", [("switch to charles", "charles"), ("Use the butler", "charles"),
                                      ("be raven", "raven"), ("change persona to Dottie.", "dottie")])
def test_switch_commands(text, pid):
    c = commands.match(text, P.load_all())
    assert c.kind == "switch" and c.persona.id == pid


def test_normal_text_is_not_a_command():
    assert commands.match("can you check the tests in the project", P.load_all()) is None


def test_clause_chunking_first_chunk_early():
    out = list(clauses(iter(["Sure", ",", " I can", " do that", ".", " It takes a moment", ", ok", "."])))
    assert out[0].startswith("Sure, I can do that") and len(out) >= 2


class FakeLLM:
    def stream(self, messages, max_tokens=160, temperature=None):
        self.messages = messages
        yield from ["Hello there, friend.", " All good."]


class FakeTTS:
    def synthesize(self, text, voice):
        yield Audio(np.zeros(10, np.float32), 16000)


class FakeOut:
    def __init__(self): self.played = 0; self.cancelled = 0
    def play(self, a): self.played += 1
    def cancel(self): self.cancelled += 1
    def wait(self): pass


class FakeSTT:
    def transcribe(self, s, sr=16000): return "hi"


def make():
    allp = P.load_all()
    llm, out = FakeLLM(), FakeOut()
    from raven2.backends.null_worker import NullWorker
    svc = Services(FakeSTT(), FakeTTS(), out, NullWorker(), Brain(llm, allp["dottie"], "Alex"))
    return Assistant(svc, allp, log=lambda m: None), llm, out


def test_loop_speaks_and_forwards_to_worker(monkeypatch, tmp_path):
    monkeypatch.setattr("raven2.config.STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr("raven2.config.HOME", tmp_path)
    a, llm, out = make()
    a.handle_text("what time is it")
    a.wait()
    assert out.played >= 1 and a.svc.worker.sent == ["what time is it"]
    assert "Dottie" in llm.messages[0]["content"] and "Alex" in llm.messages[0]["content"]


def test_switch_changes_persona_and_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr("raven2.config.STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr("raven2.config.HOME", tmp_path)
    a, llm, out = make()
    a.handle_text("switch to charles")
    a.wait()
    assert a.persona.id == "charles" and a.svc.worker.sent == []  # commands don't reach the worker
    a.handle_text("hello")
    a.wait()
    assert "Charles" in llm.messages[0]["content"]


def test_registry_swap():
    class Mine:
        def __init__(self, **_): pass
    registry.register("stt", "mine", Mine)
    assert isinstance(registry.create("stt", "mine"), Mine)
    with pytest.raises(KeyError):
        registry.create("tts", "nope")


# ── worker integration (fake worker, no SDK) ──
from raven2.interfaces import WorkerEvent


class FakeWorker:
    background = True

    def __init__(self):
        self.on_event = None
        self.sent = []
        self.pending = False
        self.answers = []

    def send(self, text): self.sent.append(text)
    def interrupt(self): pass
    def has_pending(self): return self.pending
    def answer(self, decision, text=""):
        self.answers.append((decision, text)); self.pending = False; return True
    def close(self): pass


def make_with_worker(monkeypatch, tmp_path):
    monkeypatch.setattr("raven2.config.STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr("raven2.config.HOME", tmp_path)
    allp = P.load_all()
    llm, out, w = FakeLLM(), FakeOut(), FakeWorker()
    spoken = []
    class RecTTS(FakeTTS):
        def synthesize(self, text, voice):
            spoken.append(text); return super().synthesize(text, voice)
    svc = Services(FakeSTT(), RecTTS(), out, w, Brain(llm, allp["dottie"], "Alex"))
    return Assistant(svc, allp, log=lambda m: None), w, llm, spoken


def test_worker_gets_message_and_generated_ack(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    llm.stream = lambda messages, max_tokens=160, temperature=None: (setattr(llm, "messages", messages), iter(["Running the tests."]))[1]
    a.handle_text("run the tests")
    a.wait()
    assert w.sent == ["run the tests"]
    assert spoken == ["Running the tests."]  # model output, not a canned line
    assert llm.messages[-1]["content"].startswith("run the tests")  # the ack model sees the request to guess step one


def test_ack_falls_back_to_canned_line_when_llm_fails(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    def boom(messages, max_tokens=160, temperature=None):
        raise RuntimeError("model died")
        yield
    llm.stream = boom
    a.handle_text("run the tests")
    a.wait()
    assert spoken and spoken[0] in a.persona.acks and w.sent == ["run the tests"]


def test_done_event_is_summarized_and_spoken(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.on_event(WorkerEvent("tool", "Bash: pytest"))
    w.on_event(WorkerEvent("done", "All 12 tests passed in tests/test_x.py"))
    a.wait()
    assert spoken and "12 tests" in llm.messages[-1]["content"] and "tool call" in llm.messages[-1]["content"].split("Report:")[0]  # told not to mention them


def test_permission_ask_voiced_and_answered(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.pending = True
    w.on_event(WorkerEvent("ask", "Claude wants to run rm -rf build", {"ask_type": "permission"}))
    a.wait()
    assert "Yes, always, or no?" in spoken[-1]
    a.handle_text("yeah go ahead"); a.wait()
    assert w.answers == [("allow", "")] and w.sent == []  # the answer is not a new task


def test_unclear_answer_reprompts_never_allows(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.pending = True
    w.on_event(WorkerEvent("ask", "Claude wants to edit x.py", {"ask_type": "permission"}))
    a.handle_text("hmm let me think"); a.wait()
    assert w.answers == [] and "yes or a no" in spoken[-1]
    a.handle_text("no"); a.wait()
    assert w.answers[0][0] == "deny"


def test_question_answer_passes_text_through(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.pending = True
    w.on_event(WorkerEvent("ask", "Which database should I use?", {"ask_type": "question"}))
    a.handle_text("use postgres"); a.wait()
    assert w.answers == [("text", "use postgres")]


def test_readout_speaks_after_user_barged_in(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    a.barge_in()  # cancel flag is now set from the user talking
    w.on_event(WorkerEvent("done", "Finished the refactor"))
    a.wait()
    assert spoken


def test_approval_speech_has_no_paths():
    from raven2.backends.claude_worker import _speakable
    assert _speakable("Write", {"file_path": "/Users/someone/x/note.txt"}) == "Claude wants to create note.txt"
    assert "/" not in _speakable("Bash", {"command": "rm -rf /tmp/x", "description": "Delete build folder"})
    assert _speakable("Write", {}, title="Claude wants to write foo") == "Claude wants to write foo"


def test_approval_callback_fails_closed():
    """A bug while asking must produce a deny, never an allow (regression: kwarg collision once did this)."""
    import asyncio, types
    from raven2.backends.claude_worker import ClaudeWorker
    w = ClaudeWorker()
    events = []
    w.on_event = events.append
    w._turn = 1
    ctx = types.SimpleNamespace(title=None)
    r = asyncio.run_coroutine_threadsafe(w._can_use_tool("Write", {"file_path": "/a/b.txt"}, ctx), w._loop)
    import time
    for _ in range(50):
        if events: break
        time.sleep(0.05)
    assert events[0].kind == "ask" and events[0].data["ask_type"] == "permission"
    w.answer("deny", "no")
    assert r.result(timeout=5).behavior == "deny"
    w.close()


def test_ack_that_answers_or_claims_results_is_dropped(monkeypatch, tmp_path):
    for bad in ("There are 12 files in the src folder.", "Repository uploaded.", "The tests passed.", "Task is confirmed.", "This is a data processing project.",
                "Engineering is on track.", "Project status: Currently building."):
        a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
        llm.stream = lambda messages, max_tokens=160, temperature=None, _b=bad: iter([_b])
        a.handle_text("how many files are there")
        a.wait()
        assert spoken and spoken[0] in a.persona.acks, bad  # canned fallback instead of misinformation


def test_canned_ack_mode_skips_the_model(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    a.ack_mode = "canned"
    a.handle_text("run the tests"); a.wait()
    assert spoken[0] in a.persona.acks and not hasattr(llm, "messages")


def test_startup_loads_models_then_model_speaks_intro(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    order = []
    a.svc.stt.transcribe = lambda s, sr=16000: order.append("stt") or ""
    orig = llm.stream
    llm.stream = lambda m, max_tokens=160, temperature=None: (order.append("llm"), orig(m, max_tokens, temperature))[1]
    a.startup()
    a.wait()
    assert order[0] == "stt" and "llm" in order  # models loaded before anything is spoken
    assert " ".join(spoken) == "Hello there, friend. All good." and spoken[0] != a.persona.greeting  # generated, not canned
    assert "started up" in llm.messages[-1]["content"]


def test_startup_fails_loudly_when_llm_cannot_load(monkeypatch, tmp_path):
    from raven2.core import StartupError
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    def boom(messages, max_tokens=160, temperature=None):
        raise RuntimeError("no model")
        yield
    llm.stream = boom
    with pytest.raises(StartupError, match="language model failed to load"):
        a.startup()
    assert spoken == []  # no canned greeting papering over a dead model


def test_startup_fails_loudly_when_stt_cannot_load(monkeypatch, tmp_path):
    from raven2.core import StartupError
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    def boom(s, sr=16000):
        raise OSError("model file missing")
    a.svc.stt.transcribe = boom
    with pytest.raises(StartupError, match="speech-to-text failed to load"):
        a.startup()


def test_intro_stops_after_two_sentences(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    llm.stream = lambda m, max_tokens=160, temperature=None: iter(["Up and running.", " How can I help?", " More."])
    a.startup(); a.wait()
    assert " ".join(spoken) == "Up and running. How can I help?"  # the 3rd sentence never reaches the speaker


def test_startup_without_intro_is_silent(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    a.startup(intro=False); a.wait()
    assert spoken == []


def test_limit_sentences_truncates_mid_piece():
    from raven2.brain import limit_sentences
    assert "".join(limit_sentences(iter(["Up.", " Ready? Yes", ". More."]), 2)) == "Up. Ready?"


def test_ack_accepts_first_step_phrases(monkeypatch, tmp_path):
    for good in ("Scanning the project files.", "Let me check that.", "Checking the git status.", "Very well, sir. Examining the tests."):
        a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
        llm.stream = lambda messages, max_tokens=160, temperature=None, _g=good: iter([_g])
        a.handle_text("explain this project"); a.wait()
        assert spoken == [good]


# ── RavenControls: Claude switching the live persona ──
def test_controls_list_and_switch_live_persona(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    assert w.controls is a  # the worker is handed the assistant's controls
    rows = a.list_personas()
    assert {"dottie", "charles", "raven"} <= {r["id"] for r in rows}
    assert [r["id"] for r in rows if r["active"]] == ["dottie"]
    assert all(set(r) == {"id", "name", "aliases", "active", "default"} for r in rows)
    assert [r["id"] for r in rows if r["default"]] == ["dottie"]  # names only, no personality text
    assert a.set_persona("the butler") == "Switched to Charles."
    assert a.persona.id == "charles" and a.svc.brain.persona.id == "charles"
    assert [r["id"] for r in a.list_personas() if r["active"]] == ["charles"]


def test_controls_unknown_persona_is_a_clear_error(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="Available: .*Dottie"):
        a.set_persona("gandalf")
    assert a.persona.id == "dottie"


def test_session_prompt_names_personas_but_carries_no_personality(monkeypatch, tmp_path):
    from raven2.backends.claude_worker import PERSONA_PROMPT
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    names = ", ".join(r["name"] for r in a.list_personas())
    text = PERSONA_PROMPT.format(names=names, default="Dottie")
    assert "Charles" in text and "mcp__raven__set_persona" in text and "NOT anything in the project" in text
    for p in a.personas.values():
        assert p.personality[:40] not in text and p.description not in text


def test_persona_switch_via_claude_announces_itself_without_a_readout(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.on_event(WorkerEvent("tool", "mcp__raven__set_persona"))
    a.set_persona("charles")  # called by the worker's tool handler
    w.on_event(WorkerEvent("done", "Switched to Charles."))
    a.wait()
    assert a.persona.id == "charles"
    assert " ".join(spoken) == "Hello there, friend. All good."  # the intro only; no relay of "Switched to Charles."
    assert "started up" in llm.messages[-1]["content"]


def test_tool_search_plumbing_does_not_count_as_work(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.on_event(WorkerEvent("tool", "ToolSearch"))
    w.on_event(WorkerEvent("tool", "mcp__raven__set_persona"))
    a.set_persona("charles")
    w.on_event(WorkerEvent("done", "Switched to Charles."))
    a.wait()
    assert " ".join(spoken) == "Hello there, friend. All good."


def test_persona_switch_plus_real_work_still_gets_a_readout(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.on_event(WorkerEvent("tool", "mcp__raven__set_persona"))
    a.set_persona("charles")
    w.on_event(WorkerEvent("tool", "Bash: pytest"))
    w.on_event(WorkerEvent("done", "Switched, then all tests passed."))
    a.wait()
    assert " ".join(spoken) == "Hello there, friend. All good. " * 1 + "Hello there, friend. All good."  # intro, then a readout of the real work


@pytest.mark.parametrize("pieces,expected", [
    (["The stock closed at $186", ".", "03", ", down", " 7%", " today", "."], ["The stock closed at $186.03,", "down 7% today."]),
    (["It costs $1", ",", "000", " dollars", ".", " Cheap", "."], ["It costs $1,000 dollars.", "Cheap."]),
    (["Version 3", ".", "5", " is out", "."], ["Version 3.5 is out."]),
    (["Done", "."], ["Done."]),
])
def test_numbers_are_never_split_across_speech_chunks(pieces, expected):
    assert list(clauses(iter(pieces))) == expected


def test_limit_sentences_ignores_decimal_points():
    from raven2.brain import limit_sentences
    assert "".join(limit_sentences(iter(["Up at 3", ".", "5 percent", ".", " Ready", "?", " More."]), 2)) == "Up at 3.5 percent. Ready?"


def test_limit_sentences_handles_lone_period_piece_after_digit():
    from raven2.brain import limit_sentences
    # "." arrives alone right after "3", then "5": a decimal. Then a real sentence end.
    assert "".join(limit_sentences(iter(["at 3", ".", "5", " percent", ".", " Next", "."]), 1)) == "at 3.5 percent."
    # "." after a digit that is NOT followed by a digit is a real end ("in 2024. Then")
    assert "".join(limit_sentences(iter(["in 2024", ".", " Then it", " rose", "."]), 1)) == "in 2024."


# ── status line ──
import io
from raven2.status import Status


def test_status_states_render_distinct_text():
    st = Status(stream=io.StringIO(), enabled=True)
    assert "ready" in st.text()[1]
    st.working(True); st.tool(); st.tool()
    assert "working" in st.text()[1] and "2 tools" in st.text()[1]
    st.waiting("yes, always, or no?")
    assert "waiting for your answer" in st.text()[1]
    st.done()
    assert "finished" in st.text()[1] and "2 tools" in st.text()[1]
    st.error("boom")
    assert "error: boom" in st.text()[1]
    st.listen("listening")
    assert "listening" in st.text()[1]  # the mic overlay wins while it is up...
    st.listen("")
    assert "error" in st.text()[1]  # ...and the worker state is still there after


def test_status_disabled_when_not_a_tty():
    st = Status(stream=io.StringIO())  # StringIO is not a TTY
    assert not st.enabled
    st.working(True); st.start(); st.stop()
    assert st.out.getvalue() == ""  # never writes escape codes into logs or pipes


def test_status_paints_bottom_line_and_title():
    st = Status(stream=io.StringIO(), enabled=True)
    st.working(True)
    seq = st.render_once()
    assert "\0337" in seq and "\0338" in seq and "\033]0;Raven: " in seq and "Claude working" in seq


def test_assistant_drives_status(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    a.status = Status(stream=io.StringIO(), enabled=True)
    seen = []
    a.status._redraw = lambda: seen.append(a.status.text()[1])
    a.handle_text("run the tests"); a.wait()
    assert "working" in seen[-1]
    w.pending = True
    w.on_event(WorkerEvent("tool", "Bash: pytest"))
    w.on_event(WorkerEvent("ask", "Claude wants to run a shell command", {"ask_type": "permission"}))
    assert "waiting for your answer" in seen[-1]
    a.handle_text("yes"); a.wait()
    assert "working" in seen[-1]
    w.on_event(WorkerEvent("done", "All passed")); a.wait()
    assert "finished" in seen[-1]
    w.on_event(WorkerEvent("error", "boom"))
    assert "error" in seen[-1]
