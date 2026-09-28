"""'What do those speakers look like?': when the user wants to SEE something, the answer comes back
as a picture that opens on screen, not just words."""
from __future__ import annotations

import json

import pytest

from fakes import SDP, FakeLiveWorker, FakeTransport, wait_for
from speakeasy import router
from speakeasy.prompt import builder as P

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
SHOW = '{"follow_up_task_id": null, "conversation": null, "parts": ["x"], "channel": null, "show": true}'
TELL = '{"follow_up_task_id": null, "conversation": null, "parts": ["x"], "channel": null, "show": false}'


@pytest.mark.parametrize("reply,expected", [(SHOW, True), (TELL, False),
                                            ('{"follow_up_task_id": null, "parts": ["x"], "channel": null}', False),
                                            ('{"parts": ["x"], "show": "yes"}', False)])
def test_model_show_flag_is_strict(reply, expected):
    d = router.parse_decision(reply, "what do the office speakers look like", [], [])
    assert d is not None and d.show is expected


def test_prompt_tells_the_model_what_show_means():
    system = router.route_messages("what do the speakers look like", [], [])[0]["content"]
    assert '"show"' in system and "look like" in system


def _svc(home, reply):
    from speakeasy.service import VoiceService
    workers: list[FakeLiveWorker] = []
    svc = VoiceService(home, notifier=None, start_threads=False, codex_factory=FakeTransport,
                       openai_negotiate=lambda key, payload: {"session": {"id": "sess_fake"}, "transport": {"sdp": "v=0\r\n"}},
                       openai_worker=lambda rt, i: workers.append(FakeLiveWorker(rt, i)) or workers[-1],
                       route_call=lambda m: reply)
    svc.settings.patch({"voice": {"provider": "openai"}})
    return svc, workers


def _shows(feed):
    return [p for _, kind, p in list(feed.ring) if kind == "show"]


def test_wanting_to_see_something_asks_for_a_picture_and_opens_it(home, hermes):
    folder = home / "cache" / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    photo = folder / "speakers.png"
    photo.write_bytes(PNG)
    hermes.responder = lambda prompt, sid: (f"The Kanto YU4 in black.\nMEDIA:{photo}\n"
                                            "DONE: Speakers shown\nSPOKEN: Those are the Kantos.")
    svc, workers = _svc(home, SHOW)
    try:
        created = svc.create_session({"sdp": SDP}, "req_see_1")
        workers[-1].delegate("call_see_1", "How do the office speakers you recommended look?")
        wait_for(lambda: hermes.calls)
        assert P.SHOW_IT_FOCUS.strip() in hermes.calls[-1]["input"], "the task is told to bring back a picture"
        feed = svc.interaction(created["interaction_id"]).feed
        show = wait_for(lambda: _shows(feed))[-1]
        assert show["image"] == "review" and show["task_id"] == "call_see_1"
        assert str(photo) not in json.dumps(show)
    finally:
        svc.close()


def test_ordinary_questions_do_not_pop_anything(home, hermes):
    svc, workers = _svc(home, TELL)
    try:
        created = svc.create_session({"sdp": SDP}, "req_see_2")
        workers[-1].delegate("call_see_2", "How much were the office speakers?")
        wait_for(lambda: hermes.calls)
        assert P.SHOW_IT_FOCUS.strip() not in hermes.calls[-1]["input"]
        wait_for(lambda: any(t.get("status") == "completed" for t in
                             svc.interaction(created["interaction_id"]).feed.last["tasks"] or []))
        assert not _shows(svc.interaction(created["interaction_id"]).feed)
    finally:
        svc.close()
