"""A "how's it going?" is answered from what the task reported; a continued chat reports its progress."""
import time

from fakes import wait_for
from speakeasy import continuity, router
from test_routing import start_call, tasks


def test_status_questions_are_recognized_and_real_requests_are_not():
    for q in ("Hmm. Yeah. You working on that?", "where are you at, bro", "any update on that?",
              "is it done yet", "how much longer", "what's taking so long"):
        assert router.is_status_question(q), q
    for q in ("What's the weather", "what's the status of my order from the garden shop",
              "how's it going with the hotel search", "Where are we at with the budget, and draft a note"):
        assert not router.is_status_question(q), q


def test_continued_chat_progress_reaches_the_task_and_status_is_answered_not_queued(server, service, hermes, monkeypatch):
    conv = continuity.Conversation("s_plan", "discord", "777", "thread", "777", "42", "111",
                                   "Server / #work / Planning", "Weekend planning", time.time())
    monkeypatch.setattr(continuity, "conversations_with_context", lambda db, request, **kw: [continuity.Candidate(conv, ())])
    monkeypatch.setattr(continuity, "session_busy", lambda db, sid, **k: False)
    monkeypatch.setattr(router, "aux_call", lambda messages, timeout=router.ROUTE_TIMEOUT_S:
                        '{"follow_up_task_id": null, "conversation": "c1", "parts": ["x"], "channel": null}')
    service.settings.patch({"delivery": {"target": "telegram:555", "channels": [
        {"target": "discord:111", "label": "#work", "topic": "work"}]}})
    release = []
    turns = []

    def fake_stream(base, key, c, message, callback, **k):
        turns.append(message)
        callback("run.started", {"run_id": "run_plan1"})
        callback("assistant.commentary", {"text": "Found three parks near the river. Now checking which ones have shade."})
        wait_for(lambda: release, timeout=10)
        callback("assistant.completed", {"content": "Plan's ready."})
        callback("run.completed", {})
        return True
    monkeypatch.setattr(continuity, "stream_session_chat", fake_stream)
    _, worker = start_call(server, service)
    worker.delegate("call_p", "Plan a picnic for Saturday in the planning chat")
    def milestones():
        keys = [r.idem_key for r in worker.interaction.runs.values()]
        return [e.get("text", "") for k in keys for e in ((service.store.work(idem_key=k) or {}).get("events") or [])
                if e.get("kind") == "milestone"]
    wait_for(lambda: any("three parks" in m for m in milestones()))
    worker.feed({"type": "session.output_transcript.delta", "delta": "Sure, give me a minute.", "start_ms": 3, "end_ms": 4})
    worker.delegate("call_s", "you working on that?")
    wait_for(lambda: any("how the" in c for k, _, c in worker.sent if k == "session.commentary.append"))
    said = [c for k, _, c in worker.sent if k == "session.commentary.append" and "how the" in c][-1]
    assert "three parks" in said
    assert len(turns) == 1, "a status question must never become another turn in the busy chat"
    release.append(1)
