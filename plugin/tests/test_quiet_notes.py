"""Codex voice: background notes must not draw a spoken "Mm-hmm".

Codex's voice protocol delivers every appended note as something to say, so a note that says
"don't say this" still gets a reply, and the smallest one is "Mm-hmm". Quiet notes (work started,
status, where a task went, what's on screen) are held and ride along with the next note the voice
is meant to answer, or with the finished result.
"""
from fakes import wait_for
from speakeasy.prompt import builder as P
from test_routing import start_call


def _thinking(worker):
    return [c for k, _, c in worker.sent if k == "session.thinking.append"]


def _hold(worker):
    worker.HOLD_QUIET_NOTES = True


def _run(worker, coro):
    import asyncio
    return asyncio.run_coroutine_threadsafe(coro, worker.loop).result(10)


def test_quiet_notes_are_held_then_ride_with_the_next_note(server, service):
    _, worker = start_call(server, service)
    _hold(worker)
    _run(worker, worker.quiet("started", "call_a", P.work_started_note([])))
    _run(worker, worker.quiet("status:x", "call_a", P.status_note("Reading the docs")))
    _run(worker, worker.quiet("status:x", "call_a", P.status_note("Writing the summary")))
    assert _thinking(worker) == []
    _run(worker, worker.append("session.thinking.append", "call_a", P.talk_note(worker.names)))
    sent = _thinking(worker)
    assert len(sent) == 1
    assert "Work has started" in sent[0] and "Writing the summary" in sent[0]
    assert "Reading the docs" not in sent[0]          # only the newest status per task
    assert sent[0].endswith(P.talk_note(worker.names))
    assert worker.quiet_notes == {}


def test_spoken_lines_do_not_flush_held_notes(server, service):
    _, worker = start_call(server, service)
    _hold(worker)
    _run(worker, worker.quiet("focus", None, P.focus_cleared_note(worker.names)))
    _run(worker, worker.append("session.commentary.append", None, "Here it is."))
    assert _thinking(worker) == [] and worker.quiet_notes


def test_held_notes_never_cut_a_full_report(server, service):
    _, worker = start_call(server, service)
    _hold(worker)
    _run(worker, worker.quiet("where:a", "call_a", P.where_note("a new thread")))
    report = "x" * 1900
    _run(worker, worker.append("session.thinking.append", "call_a", report))
    sent = _thinking(worker)
    assert sent[-1] == report and "new thread" in sent[0]


def test_other_voices_still_get_notes_right_away(server, service):
    _, worker = start_call(server, service)
    _run(worker, worker.quiet("started", "call_a", P.work_started_note([])))
    assert _thinking(worker) == [P.work_started_note([])]


def test_a_handoff_sends_no_lone_background_note_on_codex(server, service, hermes):
    hermes.hold = True
    _, worker = start_call(server, service)
    _hold(worker)
    worker.delegate("call_a", "Find something fun to do this weekend")
    wait_for(lambda: len(hermes.calls) >= 1)
    assert not any("Work has started" in c for c in _thinking(worker))
    assert any("Work has started" in c for c in worker.quiet_notes.values())


def test_codex_worker_holds_quiet_notes():
    from speakeasy.codex_transport import CodexSidebandWorker
    assert CodexSidebandWorker.HOLD_QUIET_NOTES is True
