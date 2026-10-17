import time
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
        self.detached = False
        self.session = {"session_id": "sess-1", "cwd": "/work/proj"}
        self.calls = []

    def session_info(self): return self.session
    def detach(self): self.detached = True; self.calls.append("detach")
    def attach(self): self.detached = False; self.calls.append("attach")

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
    a.handle_text("please run all the tests")
    a.wait()
    assert w.sent == ["please run all the tests"]
    assert spoken == ["Running the tests."]  # model output, not a canned line
    assert llm.messages[-1]["content"].startswith("please run all the tests")  # the ack model sees the request to guess step one


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
    for _ in range(300):  # generous: this thread can be slow to start when the machine is busy
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
        a.handle_text("explain what this project does"); a.wait()
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
    (["The stock closed at $186", ".", "03", ", down", " 7%", " today", "."], ["The stock closed at $186.03, down 7% today."]),
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


# ── <raven> blocks: Claude speaks verbatim, Raven only acks and reports progress ──
from raven2.raventags import split_raven, strip_tags


def test_split_raven_blocks():
    spoken, rest = split_raven("Thinking.\n<raven>Hello there.</raven> log <RAVEN>Second one.</RAVEN>")
    assert spoken == ["Hello there.", "Second one."] and rest == "Thinking.\n log"
    assert split_raven("no tags here") == ([], "no tags here")


def test_split_raven_unclosed_tag_is_still_spoken():
    assert split_raven("<raven>Cut off mid sentence")[0] == ["Cut off mid sentence"]
    assert strip_tags("<raven>Hi</raven>") == "Hi"


def test_say_event_is_spoken_verbatim_with_no_model_call_and_no_summary(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    text = "Three tests failed in the login module. Want me to look at the first one?"
    w.on_event(WorkerEvent("tool", "Bash: pytest"))
    w.on_event(WorkerEvent("say", text))
    w.on_event(WorkerEvent("done", "Three tests failed. Want me to look?"))
    a.wait()
    assert " ".join(spoken) == text            # exactly what Claude wrote
    assert not hasattr(llm, "messages")        # nothing was summarized by the local model


def test_multiple_say_blocks_speak_in_order(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.on_event(WorkerEvent("say", "On it, this may take a minute."))
    w.on_event(WorkerEvent("say", "All done. Everything passed."))
    w.on_event(WorkerEvent("done", "x"))
    a.wait()
    assert " ".join(spoken) == "On it, this may take a minute. All done. Everything passed."


def test_done_without_a_raven_block_falls_back_to_a_summary(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.on_event(WorkerEvent("done", "All 12 tests passed"))
    a.wait()
    assert spoken and "12 tests" in llm.messages[-1]["content"]


def test_verbatim_flag_resets_each_turn(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.on_event(WorkerEvent("say", "First answer."))
    w.on_event(WorkerEvent("done", "x"))
    a.handle_text("another question"); a.wait()          # new turn
    n = len(spoken)
    w.on_event(WorkerEvent("done", "Plain text with no tags"))   # this turn: no say -> fallback summary
    a.wait()
    assert len(spoken) > n


# ── progress ──
def _busy(a, w, activity=("Read: README.md",)):
    a.handle_text("do the thing"); a.wait()
    a._activity = list(activity)
    a._last_voice = 0.0


def test_progress_speaks_after_the_first_gap(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    llm.stream = lambda m, max_tokens=160, temperature=None: iter(["Reading the readme."])
    _busy(a, w); n = len(spoken)
    a._progress_tick(a.progress_first - 1); a.wait()
    assert len(spoken) == n                                # too soon
    a._progress_tick(a.progress_first + 1); a.wait()
    assert spoken[-1] == "Reading the readme."


def test_progress_is_quiet_without_new_activity_or_while_waiting(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    llm.stream = lambda m, max_tokens=160, temperature=None: iter(["Running the tests."])
    _busy(a, w, activity=()); n = len(spoken)
    a._progress_tick(999); a.wait()
    assert len(spoken) == n                                # no activity -> silence
    a._activity = ["Bash: pytest"]; w.pending = True
    a._progress_tick(999); a.wait()
    assert len(spoken) == n                                # waiting on the user -> never interrupt
    w.pending = False
    a._progress_tick(999); a.wait()
    assert spoken[-1] == "Running the tests."


def test_progress_never_talks_over_speech_and_stops_when_done(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    llm.stream = lambda m, max_tokens=160, temperature=None: iter(["Running the tests."])
    _busy(a, w); n = len(spoken)
    a._pending = 1                                         # something is being spoken
    a._progress_tick(999)
    a._pending = 0
    a.wait(); assert len(spoken) == n
    w.on_event(WorkerEvent("done", "x")); a.wait()
    a._activity = ["Bash: pytest"]; m = len(spoken)
    a._progress_tick(999); a.wait()
    assert len(spoken) == m                                # turn is over


def test_progress_drops_results_and_statements(monkeypatch, tmp_path):
    for bad in ("The tests passed.", "Everything is fine.", "Project status: good."):
        a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
        llm.stream = lambda m, max_tokens=160, temperature=None, _b=bad: iter([_b])
        _busy(a, w); n = len(spoken)
        a._progress_tick(999); a.wait()
        assert len(spoken) == n, bad


def test_progress_labels_hide_paths():
    from raven2.core import _short_label
    assert _short_label("Read: /Users/someone/Development/raven/README.md") == "Read: README.md"


# ── the worker: prompt rules, persona context, tag parsing ──
def test_session_prompt_encodes_the_voice_and_brevity_rules():
    from raven2.backends.claude_worker import SESSION_PROMPT, DISALLOWED_TOOLS
    for needle in ("<raven>", "LISTENING", "verbatim", "BE BRIEF", "ONE step at a", "narrow", "ONE question",
                   "END the turn with a <raven>"):
        assert needle.lower() in SESSION_PROMPT.lower() or needle in SESSION_PROMPT, needle
    assert {"AskUserQuestion", "EnterPlanMode", "ExitPlanMode"} <= set(DISALLOWED_TOOLS)


def test_persona_reaches_claude_as_hook_context_not_in_the_message(monkeypatch, tmp_path):
    import asyncio
    from raven2.backends.claude_worker import ClaudeWorker
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    cw = ClaudeWorker()
    sent = []
    async def fake_send(text, turn): sent.append(text)
    cw._send = fake_send
    cw.controls = a
    cw.send("what's up"); time.sleep(0.2)
    assert sent == ["what's up"]                       # the user's message is passed through untouched
    def hook():
        return asyncio.run_coroutine_threadsafe(cw._persona_hook({}, None, None), cw._loop).result(5)
    out = hook()["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit" and "Active persona: Dottie" in out["additionalContext"]
    a.set_persona("charles")                           # follows a live switch, no restart needed
    assert "Active persona: Charles" in hook()["hookSpecificOutput"]["additionalContext"]
    cw.controls = None
    assert hook() == {}
    cw.close()


def test_worker_turns_raven_blocks_into_say_events():
    import asyncio
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
    from raven2.backends.claude_worker import ClaudeWorker
    msgs = [
        AssistantMessage(content=[TextBlock("Checking.\n<raven>On it, one moment.</raven>"),
                                  ToolUseBlock("1", "Bash", {"command": "pytest"})], model="m"),
        AssistantMessage(content=[TextBlock("<raven>All twelve tests passed.</raven>")], model="m"),
        ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                      session_id="s", result="<raven>All twelve tests passed.</raven>"),
    ]
    class FakeClient:
        async def receive_response(self):
            for m in msgs: yield m
    cw = ClaudeWorker()
    events = []
    cw.on_event = events.append
    cw._turn = 1
    asyncio.run_coroutine_threadsafe(cw._drain(FakeClient(), 1), cw._loop).result(5)
    kinds = [(e.kind, e.text) for e in events]
    assert ("say", "On it, one moment.") in kinds and ("say", "All twelve tests passed.") in kinds
    assert ("text", "Checking.") in kinds                      # untagged prose stays log-only
    assert kinds[-1] == ("done", "All twelve tests passed.")   # tags stripped from the final result
    assert any(k == "tool" for k, _ in kinds)
    cw.close()


def test_stale_progress_is_dropped_if_claude_spoke_while_it_was_generated(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    def slow(messages, max_tokens=160, temperature=None):
        w.on_event(WorkerEvent("say", "Here is your answer."))   # Claude answers while the line is generating
        yield "Reading the readme."
    llm.stream = slow
    _busy(a, w)
    a._progress_tick(999); a.wait()
    assert "Reading the readme." not in spoken and "Here is your answer." in " ".join(spoken)


def test_short_continuations_get_a_canned_ack_not_a_guess(monkeypatch, tmp_path):
    for text in ("next", "yes please", "go on then"):
        a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
        a.handle_text(text); a.wait()
        assert spoken[0] in a.persona.acks and not hasattr(llm, "messages"), text
        assert w.sent == [text]                         # still forwarded to Claude untouched
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    llm.stream = lambda m, max_tokens=160, temperature=None: iter(["Running the tests."])
    a.handle_text("run all the tests now"); a.wait()
    assert spoken == ["Running the tests."]             # real requests still get the first-step guess


# ── open the Claude session in a terminal ──
import subprocess
from raven2 import terminal as T


def test_terminal_command_resumes_the_session_in_its_project_dir():
    cmd = T.claude_command("/Users/someone/My Projects/it's", "abc-123", "/bin/claude")
    assert cmd == "cd '/Users/someone/My Projects/it'\"'\"'s' && env -u RAVEN /bin/claude --resume abc-123"


def test_applescript_quotes_and_targets():
    cmd = T.claude_command('/w/"x"', "id1", "/bin/claude")
    t = T.script_for(cmd, "Terminal")
    assert 'tell application "Terminal"' in t and "do script" in t and '\\"x\\"' in t
    assert 'tell application "iTerm"' in T.script_for(cmd, "iTerm")


def test_open_in_terminal_reports_osascript_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "not allowed"))
    with pytest.raises(RuntimeError, match="not allowed"):
        T.open_in_terminal("echo hi")


def test_open_manual_session_pauses_raven_and_opens_the_terminal(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    opened = []
    monkeypatch.setattr(T, "open_in_terminal", lambda cmd, app="Terminal": opened.append(cmd))
    monkeypatch.setattr(T.shutil, "which", lambda n: "/bin/claude")
    a.status = Status(stream=io.StringIO(), enabled=True)
    a.handle_text("do a big task for me please"); a.wait()
    info = a.open_manual_session()
    assert info["session_id"] == "sess-1" and w.calls == ["detach"] and a.manual
    assert opened == ["cd /work/proj && env -u RAVEN /bin/claude --resume sess-1"]
    assert "terminal" in a.status.text()[1] and not a._working
    n = len(w.sent)
    a.handle_text("hello are you there"); a.wait()
    assert len(w.sent) == n and "open in the terminal" in " ".join(spoken)   # nothing reaches the session
    a.hand_back()
    assert not a.manual and w.calls == ["detach", "attach"]
    a.handle_text("ok continue with the task now"); a.wait()
    assert w.sent[-1] == "ok continue with the task now"


def test_open_manual_session_without_a_session_yet(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.session = None
    with pytest.raises(RuntimeError, match="no Claude session yet"):
        a.open_manual_session()
    assert w.calls == [] and not a.manual


def test_if_the_terminal_wont_open_raven_is_not_left_paused(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    def boom(cmd, app="Terminal"): raise RuntimeError("couldn't open Terminal: not allowed")
    monkeypatch.setattr(T, "open_in_terminal", boom)
    monkeypatch.setattr(T.shutil, "which", lambda n: "/bin/claude")
    with pytest.raises(RuntimeError):
        a.open_manual_session()
    assert w.calls == ["detach", "attach"] and not a.manual


def test_missing_claude_binary_fails_before_pausing(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    monkeypatch.setattr(T.shutil, "which", lambda n: None)
    with pytest.raises(FileNotFoundError):
        a.open_manual_session()
    assert w.calls == []


@pytest.mark.parametrize("text,kind", [
    ("open Claude in the terminal", "open_terminal"), ("take me to the claude session", "open_terminal"),
    ("let me see the session", "open_terminal"), ("hand it back", "handback"),
    ("I'm done in the terminal", "handback"), ("you can have claude back", "handback")])
def test_voice_commands_for_manual_session(text, kind):
    assert commands.match(text, P.load_all()).kind == kind


@pytest.mark.parametrize("text", ["open the readme file", "why does the terminal crash on startup",
                                  "please open the config and change the port to nine thousand"])
def test_ordinary_requests_do_not_trigger_terminal_commands(text):
    assert commands.match(text, P.load_all()) is None


def test_voice_command_runs_the_takeover(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    monkeypatch.setattr(T, "open_in_terminal", lambda cmd, app="Terminal": None)
    monkeypatch.setattr(T.shutil, "which", lambda n: "/bin/claude")
    a.handle_text("open claude in the terminal"); a.wait()
    assert a.manual and "Opening Claude in the terminal." in spoken and w.sent == []
    a.handle_text("hand it back"); a.wait()
    assert not a.manual and "Back in control." in spoken


def test_commas_do_not_split_a_sentence_into_separate_utterances():
    text = ("Jordan Alexander Rivers, goes by Alex, Springfield Illinois address, born June second, nineteen "
            "eighty-eight, from Peoria, Illinois. I inferred Alexander from the spelling, so correct me if "
            "that's wrong.")
    from raven2.brain import verbatim_chunks
    chunks = list(verbatim_chunks(text))
    assert " ".join(chunks) == text                      # nothing lost or reordered
    assert all(c.rstrip()[-1] in ".!?" for c in chunks)  # every chunk ends at a real sentence end
    assert len(chunks) <= 2


def test_streamed_tokens_also_chunk_at_sentence_ends_only():
    pieces = ["Systems", " initialized", ",", " all", " green", ".", " Available", " to", " assist", "."]
    assert list(clauses(iter(pieces))) == ["Systems initialized, all green.", "Available to assist."]


def test_a_very_long_run_with_no_sentence_end_falls_back_to_a_comma_cut():
    long = ", ".join(f"item number {i}" for i in range(30))   # ~450 chars, no sentence end
    chunks = list(clauses(iter([long])))
    assert len(chunks) >= 2 and " ".join(chunks) == long
    assert all(len(c) <= 170 for c in chunks)


# ── ordering: Raven's ack is always first and quick; Claude waits its turn ──
import threading


class InstantWorker(FakeWorker):
    """Claude that answers (or asks) the instant it receives a message, before the ack could be generated."""
    def __init__(self, events):
        super().__init__(); self.events = events

    def send(self, text):
        self.sent.append(text)
        for ev in self.events:
            self.pending = self.pending or ev.kind == "ask"   # a permission is outstanding once it is asked
            self.on_event(ev)


def make_instant(monkeypatch, tmp_path, events, ack_text="Checking the files.", ack_delay=0.0):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    iw = InstantWorker(events)
    iw.on_event = a._on_worker_event
    a.svc.worker = iw
    iw.controls = a
    def stream(messages, max_tokens=160, temperature=None):
        time.sleep(ack_delay)
        yield ack_text
    llm.stream = stream
    return a, iw, llm, spoken


def test_fast_claude_answer_still_speaks_after_the_ack(monkeypatch, tmp_path):
    a, w, llm, spoken = make_instant(monkeypatch, tmp_path, [WorkerEvent("say", "The answer is forty two."),
                                                              WorkerEvent("done", "x")], ack_delay=0.4)
    a.handle_text("what is the answer to everything please"); a.wait()
    assert spoken == ["Checking the files.", "The answer is forty two."]   # ack first, even though Claude was faster


def test_fast_permission_request_does_not_cancel_the_ack(monkeypatch, tmp_path):
    a, w, llm, spoken = make_instant(monkeypatch, tmp_path,
                                     [WorkerEvent("ask", "Claude wants to search the web", {"ask_type": "permission"})],
                                     ack_delay=0.3)
    a.handle_text("find the stock price for me today"); a.wait()
    assert spoken[0] == "Checking the files." and spoken[1].startswith("Claude wants to search the web")


def test_fast_persona_switch_does_not_cancel_the_ack(monkeypatch, tmp_path):
    a, w, llm, spoken = make_instant(monkeypatch, tmp_path, [], ack_delay=0.3)
    a.handle_text("make it the butler voice please now")
    time.sleep(0.05)
    a.set_persona("charles")        # Claude's tool call lands while the ack is still being generated
    a.wait()
    assert spoken[0] == "Checking the files." and a.persona.id == "charles" and len(spoken) >= 2


def test_slow_model_gets_a_canned_ack_within_the_deadline(monkeypatch, tmp_path):
    a, w, llm, spoken = make_instant(monkeypatch, tmp_path, [WorkerEvent("say", "Here you go.")], ack_delay=3.0)
    a.ack_deadline = 0.2
    t0 = time.time()
    a.handle_text("explain what this project does please"); a.wait()
    assert spoken[0] in a.persona.acks and spoken[-1] == "Here you go."
    assert time.time() - t0 < 2.0                                          # didn't wait for the slow model


def test_failing_model_falls_back_to_canned_ack(monkeypatch, tmp_path):
    a, w, llm, spoken = make_instant(monkeypatch, tmp_path, [WorkerEvent("say", "Done here.")])
    def boom(messages, max_tokens=160, temperature=None):
        raise RuntimeError("model died"); yield
    llm.stream = boom
    a.handle_text("explain what this project does please"); a.wait()
    assert spoken[0] in a.persona.acks and spoken[-1] == "Done here."


def test_barge_in_drops_queued_claude_speech(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    gate = threading.Event()
    real = a.svc.tts.synthesize
    def synth(text, voice):
        if text == "first": gate.wait(2)          # the first utterance is "playing"
        return real(text, voice)
    a.svc.tts.synthesize = synth
    a.say("first", cancel_current=True)
    a.say("second", cancel_current=False); a.say("third", cancel_current=False)
    time.sleep(0.05)
    a.barge_in()                                  # the user presses the key
    gate.set(); a.wait()
    assert "second" not in spoken and "third" not in spoken and a._pending == 0
    assert a.svc.out.cancelled >= 1               # and the audio already playing is cut off immediately


def test_cancel_current_replaces_queued_speech(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    gate = threading.Event()
    real = a.svc.tts.synthesize
    def slow(text, voice):
        if text == "first": gate.wait(2)
        return real(text, voice)
    a.svc.tts.synthesize = slow
    a.say("first"); a.say("queued", cancel_current=False)
    time.sleep(0.05)
    a.say("replacement")            # default cancel_current=True: supersedes first and queued
    gate.set(); a.wait()
    assert "queued" not in spoken and spoken[-1] == "replacement"


# ── small talk goes to Raven, not Claude ──
CHIT = ["how are you", "How are you doing today?", "hey Dottie, how are you", "good job", "Thanks!",
        "thank you so much", "you're doing great", "that was awesome", "who are you", "hi", "good morning Raven",
        "I love you", "what's up", "nice work", "you are the best", "are you there"]
TASKS = ["thanks, can you run the tests", "how's it going with the build", "good job on the tests, now deploy",
         "what's up with the failing test", "how are the tests doing", "hello can you check the logs",
         "you did great, commit it", "who are the contributors to this project", "nice work, now fix the bug in login",
         "run the tests", "what does this project do"]


@pytest.mark.parametrize("text", CHIT)
def test_conversational_talk_is_recognized(text):
    assert commands.match(text, P.load_all()).kind == "chat"


@pytest.mark.parametrize("text", TASKS)
def test_anything_with_a_task_is_not_small_talk(text):
    c = commands.match(text, P.load_all())
    assert c is None or c.kind != "chat"


def test_small_talk_gets_an_instant_local_reply_and_never_reaches_claude(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    a.handle_text("hey how are you doing today"); a.wait()
    assert w.sent == []                                          # not forwarded
    assert spoken == ["Hello there, friend."]                    # one short sentence, from the local model
    assert "how are you doing today" in str(llm.messages) and "small talk" in llm.messages[0]["content"]


def test_small_talk_does_not_interrupt_a_running_task(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    a.handle_text("please refactor the whole login module now"); a.wait()
    a.status = Status(stream=io.StringIO(), enabled=True)
    sent = list(w.sent)
    a.handle_text("good job"); a.wait()
    assert w.sent == sent and a._working                         # Claude is untouched and still working


def test_yes_thanks_still_answers_a_pending_permission(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.pending = True
    w.on_event(WorkerEvent("ask", "Claude wants to edit x", {"ask_type": "permission"}))
    a.handle_text("thanks"); a.wait()                            # small talk while a permission is open
    assert w.answers == [] and w.pending                         # not misread as a decision
    a.handle_text("yes thanks"); a.wait()
    assert w.answers == [("allow", "")]


# ── never talk over the user ──
def test_speech_waits_while_the_user_is_talking_then_resumes_on_a_stray_tap(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    a._st("listen", "listening")                                 # key held down
    a.say("Claude's answer", cancel_current=False)
    time.sleep(0.3)
    assert spoken == []                                          # held back; not talking over the user
    a._st("listen", "")                                          # accidental tap: released with no message
    a.wait()
    assert spoken == ["Claude's answer"]


def test_speech_held_while_listening_is_dropped_when_the_message_arrives(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    a._st("listen", "listening")
    a.say("stale answer", cancel_current=False)
    time.sleep(0.1)
    a._st("listen", "transcribing")
    a.handle_text("what is two plus two exactly")                # the new message supersedes it
    a._st("listen", ""); a.wait()
    assert "stale answer" not in spoken


def test_a_broken_utterance_never_silences_later_speech(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    logs = []
    a.log = logs.append
    def explode(): raise RuntimeError("bug while creating the utterance")
    a._start(explode, cancel_current=False)
    def explode_midway():
        yield "fine so far."
        raise ValueError("bug midway")
    a._start(explode_midway, cancel_current=False)
    a.say("still talking", cancel_current=False)
    a.wait()
    assert "still talking" in spoken and "fine so far." in spoken       # the speaker thread survived both
    assert any("bug while creating" in l for l in logs) and any("bug midway" in l for l in logs)
    assert a._pending == 0


def test_chat_is_a_method_of_brain():
    from raven2.brain import Brain
    assert callable(getattr(Brain, "chat")) and callable(getattr(Brain, "progress")) and callable(getattr(Brain, "intro"))


# ── hand-back really ends the hand-driven session ──
import signal as _signal
import sys as _sys


def _spawn(sid, extra=(), ignore_term=False):
    code = ("import signal,time;" + ("signal.signal(signal.SIGTERM, signal.SIG_IGN);" if ignore_term else "")
            + "time.sleep(120)")
    return subprocess.Popen([_sys.executable, "-c", code, "--resume", sid, *extra])


def test_stop_session_ends_the_manual_claude_process():
    sid = "sess-stop-1"
    p = _spawn(sid)
    time.sleep(0.5)
    assert p.pid in T.session_pids(sid)
    T.stop_session(sid, timeout=5)
    assert p.wait(timeout=5) is not None and T.session_pids(sid) == []


def test_stop_session_escalates_to_kill_when_term_is_ignored():
    sid = "sess-stop-2"
    p = _spawn(sid, ignore_term=True)
    time.sleep(0.5)
    T.stop_session(sid, timeout=1)
    assert p.wait(timeout=5) == -_signal.SIGKILL


def test_stop_session_never_touches_the_sdk_workers_own_process():
    sid = "sess-stop-3"
    worker = _spawn(sid, extra=("--input-format", "stream-json"))     # how the SDK launches claude
    time.sleep(0.5)
    try:
        assert worker.pid not in T.session_pids(sid)
        T.stop_session(sid)                                            # nothing to stop: must not raise
        assert worker.poll() is None                                   # and the worker is still alive
    finally:
        worker.kill()


def test_stop_session_with_nothing_running_is_fine():
    T.stop_session("sess-that-does-not-exist")


def test_open_in_terminal_returns_a_handle_for_closing_the_window(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "4812|/dev/ttys005\n", ""))
    h = T.open_in_terminal("echo hi")
    assert (h.app, h.window_id, h.tty) == ("Terminal", "4812", "/dev/ttys005")
    assert "first window whose id is 4812" in T.close_script_for(h)


def test_close_window_is_a_noop_without_a_handle(monkeypatch):
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    T.close_window(None); T.close_window(T.Handle("Terminal", ""))
    assert called == []


def test_hand_back_stops_the_session_then_closes_the_window_then_resumes(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    order = []
    monkeypatch.setattr(T, "open_in_terminal", lambda cmd, app="Terminal": T.Handle("Terminal", "77", "/dev/ttys009"))
    monkeypatch.setattr(T.shutil, "which", lambda n: "/bin/claude")
    monkeypatch.setattr(T, "stop_session", lambda sid, timeout=8.0: order.append(("stop", sid)))
    monkeypatch.setattr(T, "close_window", lambda h: order.append(("close", h.window_id if h else None)))
    orig_attach = w.attach
    w.attach = lambda: (order.append(("attach",)), orig_attach())
    a.open_manual_session()
    a.hand_back()
    assert order == [("stop", "sess-1"), ("close", "77"), ("attach",)]   # never resume while it still runs
    assert not a.manual and a._manual_terminal is None


def test_hand_back_refuses_to_resume_if_the_session_cannot_be_stopped(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    monkeypatch.setattr(T, "open_in_terminal", lambda cmd, app="Terminal": T.Handle("Terminal", "77"))
    monkeypatch.setattr(T.shutil, "which", lambda n: "/bin/claude")
    def stuck(sid, timeout=8.0): raise RuntimeError("the Claude session is still running in the terminal")
    closed = []
    monkeypatch.setattr(T, "stop_session", stuck)
    monkeypatch.setattr(T, "close_window", lambda h: closed.append(h))
    a.open_manual_session()
    with pytest.raises(RuntimeError, match="still running"):
        a.hand_back()
    assert a.manual and closed == []                    # still paused, window untouched, nothing resumed


def test_detach_reruns_the_same_command_in_the_background_without_the_flag():
    from raven2 import cli
    argv = ["--worker", "claude", "--cwd", "/w", "ui", "--detach"]
    assert cli.detached_argv(argv) == [_sys.executable, "-m", "raven2.cli", "--worker", "claude", "--cwd", "/w", "ui"]


# ── switching projects in place ──
def test_open_project_resets_the_session_but_keeps_everything_loaded(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    opened = []
    w.open_project = lambda cwd: opened.append(cwd)
    a.svc.brain.history += [{"role": "user", "content": "old project talk"}, {"role": "assistant", "content": "ok"}]
    proj = tmp_path / "newproj"; proj.mkdir()
    a._working, a._activity = True, ["Read: x"]
    assert a.open_project(str(proj)) == str(proj)
    assert opened == [str(proj)] and a.project_dir == str(proj)
    assert a.svc.brain.history == [] and not a._working and a._activity == []   # clean slate for the new project


def test_open_project_refuses_a_missing_folder_and_while_a_terminal_owns_the_session(monkeypatch, tmp_path):
    a, w, llm, spoken = make_with_worker(monkeypatch, tmp_path)
    w.open_project = lambda cwd: (_ for _ in ()).throw(AssertionError("must not be called"))
    with pytest.raises(FileNotFoundError, match="doesn't exist"):
        a.open_project(str(tmp_path / "nope"))
    w.detached = True
    with pytest.raises(RuntimeError, match="Hand Claude back"):
        a.open_project(str(tmp_path))


def test_worker_open_project_starts_a_fresh_session_in_the_new_folder(tmp_path):
    from raven2.backends.claude_worker import ClaudeWorker
    cw = ClaudeWorker(cwd=str(tmp_path))
    cw.session_id, cw._resume_next, cw._always = "old-session", True, {"Bash"}
    new = tmp_path / "other"; new.mkdir()
    cw.open_project(str(new))
    assert cw._cwd == str(new) and cw.session_id is None and cw._resume_next is False and cw._always == set()
    assert cw.session_info() is None                     # no session until the first message in the new folder
    with pytest.raises(FileNotFoundError):
        cw.open_project(str(tmp_path / "missing"))
    cw.close()


# ── speech speed, loudness ──
def _capture_piper(monkeypatch, speed=None, raw_peak=0.0, **voice_params):
    """Run PiperTTS against a fake voice; returns (the SynthesisConfig of the real utterance, the tts)."""
    from raven2.backends.piper_tts import PiperTTS
    from raven2.interfaces import VoiceSpec
    seen = {"calls": 0}
    amp = int(raw_peak * 32768)
    class FakeVoice:
        config = type("C", (), {"sample_rate": 22050})()
        def synthesize(self, text, cfg):
            seen["calls"] += 1
            seen["cfg"] = cfg                               # the last call is the real utterance
            yield type("Chunk", (), {"audio_int16_array": np.full(2205, amp, np.int16)})()
    tts = PiperTTS() if speed is None else PiperTTS(speed=speed)
    monkeypatch.setattr(tts, "_voice", lambda vid: FakeVoice())
    effects = voice_params.pop("effects", {})
    list(tts.synthesize("hello", VoiceSpec("piper", "any-voice", voice_params, effects)))
    tts.seen = seen
    return seen["cfg"], tts


def test_length_divisor_delivers_the_requested_real_speedup():
    from raven2.backends.piper_tts import FIXED_SHARE, length_divisor
    assert length_divisor(1.0) == pytest.approx(1.0)
    for speed in (0.5, 0.8, 1.0, 1.3, 1.5, 2.0):
        d = length_divisor(speed)
        actual = 1 / (FIXED_SHARE + (1 - FIXED_SHARE) / d)       # what the audio really does (measured model)
        assert actual == pytest.approx(speed), speed
    assert length_divisor(1.3) > 1.3                              # length_scale alone under-delivers, so it's compensated
    assert length_divisor(50) == length_divisor(2.0) and length_divisor(0.01) == length_divisor(0.5)   # bounded


def test_default_speed_is_30_percent_faster(monkeypatch):
    from raven2.backends.piper_tts import length_divisor
    cfg, tts = _capture_piper(monkeypatch, length_scale=1.0)
    assert tts.speed == 1.3 and cfg.length_scale == pytest.approx(1 / length_divisor(1.3)) and cfg.length_scale < 0.75


def test_personas_keep_their_relative_pacing(monkeypatch):
    dottie = _capture_piper(monkeypatch, length_scale=0.975)[0].length_scale
    raven = _capture_piper(monkeypatch, length_scale=1.2)[0].length_scale
    assert raven / dottie == pytest.approx(1.2 / 0.975)          # Raven is still slower and sultrier than Dottie


def test_speed_is_configurable_and_bounded(monkeypatch):
    assert _capture_piper(monkeypatch, speed=1.0, length_scale=1.0)[0].length_scale == pytest.approx(1.0)
    assert _capture_piper(monkeypatch, speed=50, length_scale=1.0)[1].speed == 2.0
    assert _capture_piper(monkeypatch, speed=0.01, length_scale=1.0)[1].speed == 0.5


def test_pitch_shift_still_does_not_change_speed(monkeypatch):
    base = _capture_piper(monkeypatch, length_scale=1.0)[0].length_scale
    down = _capture_piper(monkeypatch, length_scale=1.0, effects={"pitch": -3})[0].length_scale
    assert down == pytest.approx(base * 2 ** (-3 / 12))


def test_default_profile_sets_the_speed_and_config_can_override_it(tmp_path):
    from raven2 import config
    assert config.load_profile("intel-cpu", path=tmp_path / "none.toml")["tts"]["speed"] == 1.3
    (tmp_path / "c.toml").write_text('[tts]\nspeed = 1.0\n')
    prof = config.load_profile("intel-cpu", path=tmp_path / "c.toml")
    assert prof["tts"]["speed"] == 1.0 and prof["tts"]["backend"] == "piper"


def test_each_voice_is_gain_matched_to_its_own_level(monkeypatch):
    """Regression: one fixed boost clipped louder voices (Charles raw peak 0.72 x3 = 2.15)."""
    from raven2.backends.piper_tts import TARGET_PEAK
    quiet, q = _capture_piper(monkeypatch, raw_peak=0.26)
    loud, l = _capture_piper(monkeypatch, raw_peak=0.72)
    assert quiet.volume == pytest.approx(TARGET_PEAK / 0.26, rel=0.01) and loud.volume == pytest.approx(TARGET_PEAK / 0.72, rel=0.01)
    assert loud.volume < quiet.volume                             # the louder voice gets less gain
    assert 0.72 * loud.volume <= TARGET_PEAK + 0.01               # and ends up at the target, not past full scale


def test_gain_is_bounded_measured_once_and_overridable(monkeypatch):
    cfg, tts = _capture_piper(monkeypatch, raw_peak=0.01)
    assert cfg.volume == 6.0                                      # a very quiet voice is capped
    cfg, tts = _capture_piper(monkeypatch, raw_peak=0.0)
    assert cfg.volume == 3.0                                      # silence in the calibration: a sane default
    cfg, tts = _capture_piper(monkeypatch, raw_peak=0.26)
    calls_before = tts.seen["calls"]
    from raven2.interfaces import VoiceSpec
    list(tts.synthesize("again", VoiceSpec("piper", "any-voice", {}, {})))
    assert tts.seen["calls"] == calls_before + 1                  # the second utterance did not re-calibrate
    assert _capture_piper(monkeypatch, raw_peak=0.26, volume=1.5)[0].volume == 1.5     # an explicit persona volume wins


def test_soft_limit_is_transparent_below_the_knee_and_never_clips():
    from raven2.audio_fx import soft_limit
    quiet = np.linspace(-0.79, 0.79, 101).astype(np.float32)
    assert np.array_equal(soft_limit(quiet), quiet)               # untouched below the knee (tanh bent everything)
    loud = np.array([0.8, 0.9, 1.0, 1.5, 5.0, -0.9, -5.0], np.float32)
    out = soft_limit(loud)
    assert np.all(np.abs(out) <= 1.0) and np.all(np.sign(out) == np.sign(loud))   # approaches full scale, never past it
    assert out[0] == pytest.approx(0.8) and out[1] < 0.9                          # continuous at the knee, rounds peaks off
    assert np.all(np.diff(soft_limit(np.linspace(0.8, 1.5, 50, dtype=np.float32))) > 0)   # rises smoothly through the useful range
    assert np.all(np.diff(soft_limit(np.linspace(0.8, 5, 50, dtype=np.float32))) >= 0)    # and never turns back down


def test_fx_chain_with_only_gain_leaves_normal_levels_alone():
    from raven2.audio_fx import FxChain
    x = (np.sin(np.linspace(0, 200, 5000)) * 0.6).astype(np.float32)
    assert np.allclose(FxChain(22050, {"gain": 1.0}).process(x), x)    # Charles's chain: no coloring at normal level


# Real voices (skipped when the voice files aren't installed): the guard against the actual symptoms.
def _real(pid):
    from raven2 import personas as P
    from raven2.backends.piper_tts import PiperTTS, ensure_voice
    from raven2.interfaces import AssetMissing
    persona = P.load_all()[pid]
    try:
        ensure_voice(persona.voice.model)
    except AssetMissing:
        pytest.skip(f"{pid}'s voice isn't installed")
    return persona.voice, PiperTTS


LINES = ["The status module pins a one-line status bar to the bottom of the terminal. It shows what Claude is doing.",
         "Mmm, I'm up, baby. Ready when you are. Anything for you, daddy!", "Three tests failed. Want me to look at the first one?"]


@pytest.mark.parametrize("pid", ["dottie", "charles", "raven"])
def test_real_voices_never_clip_and_sit_at_a_healthy_level(pid):
    voice, TTS = _real(pid)
    tts = TTS()
    for line in LINES:
        x = np.concatenate([c.samples for c in tts.synthesize(line, voice)])
        assert int((np.abs(x) >= 0.999).sum()) == 0, (pid, line)  # clipping is what sounded like crackle
        assert 0.4 < float(np.abs(x).max()) < 0.97, (pid, line)


@pytest.mark.parametrize("pid", ["dottie", "charles"])
def test_real_voices_really_are_about_30_percent_faster(pid):
    voice, TTS = _real(pid)
    text = LINES[0]
    def secs(speed):
        chunks = list(TTS(speed=speed).synthesize(text, voice))
        return sum(len(c.samples) for c in chunks) / chunks[0].sample_rate
    assert 1.22 < secs(1.0) / secs(1.3) < 1.38                    # was only ~1.18x before calibrating
