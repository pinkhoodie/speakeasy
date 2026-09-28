"""'Waiting for <name>' forever: anything that breaks between the voice's "on it" and the task
starting must end as a visible failure, never a task that silently never begins."""
from __future__ import annotations

import pytest

from fakes import SDP, FakeLiveWorker, FakeTransport, wait_for
from speakeasy.prompt import builder as P


def _svc(home, **kw):
    from speakeasy.service import VoiceService
    workers: list[FakeLiveWorker] = []
    svc = VoiceService(home, notifier=None, start_threads=False, codex_factory=FakeTransport,
                       openai_negotiate=lambda key, payload: {"session": {"id": "sess_fake"}, "transport": {"sdp": "v=0\r\n"}},
                       openai_worker=lambda rt, i: workers.append(FakeLiveWorker(rt, i)) or workers[-1], **kw)
    svc.settings.patch({"voice": {"provider": "openai"}})
    return svc, workers


def _boom(*a, **k):
    raise RuntimeError("routing exploded")


def test_a_routing_crash_still_starts_the_work(home, hermes):
    svc, workers = _svc(home, route_call=_boom)
    try:
        created = svc.create_session({"sdp": SDP}, "req_stuck_1")
        workers[-1].delegate("call_stuck_1", "Book a table for two at eight")
        wait_for(lambda: hermes.calls, timeout=10)
        tasks = svc.interaction(created["interaction_id"]).feed.last["tasks"]
        assert tasks and tasks[0]["task_id"] == "call_stuck_1"
    finally:
        svc.close()


def test_a_crash_before_the_task_exists_fails_visibly(home, hermes, monkeypatch):
    from speakeasy import calls
    svc, workers = _svc(home, route_call=lambda m: None)
    monkeypatch.setattr(calls.SidebandWorker, "conversation_candidates", _async_boom)
    try:
        created = svc.create_session({"sdp": SDP}, "req_stuck_2")
        worker = workers[-1]
        worker.delegate("call_stuck_2", "Book a table for two at eight")
        feed = svc.interaction(created["interaction_id"]).feed
        task = wait_for(lambda: next((t for t in feed.last.get("tasks") or []
                                      if t["task_id"] == "call_stuck_2" and t["status"] == "failed"), None), timeout=10)
        assert task["status"] == "failed"
        wait_for(lambda: any(P.FAILED_SPOKEN in str(x) for x in worker.sent), timeout=5)
    finally:
        svc.close()


async def _async_boom(*a, **k):
    raise RuntimeError("state.db locked")
