"""First-call tour: a flag on the session request adds one-time tour steps to the voice's
instructions; the panel's Skip button tells the live call to drop it."""
from __future__ import annotations

from fakes import SDP, http
from speakeasy.prompt import builder as P


def open_call(server, service, body, key):
    seen = {}
    real = service._openai_negotiate
    service._openai_negotiate = lambda k, payload: (seen.update(payload=payload), real(k, payload))[1]
    status, session = http(server.base_url, "POST", "/voice/sessions", {"sdp": SDP, **body}, server.token,
                           {"Idempotency-Key": key})
    service._openai_negotiate = real
    return status, session, seen.get("payload", {}).get("session", {}).get("instructions", "")


def test_plain_call_has_no_tour(server, service):
    status, _, instructions = open_call(server, service, {}, "req_plain")
    assert status == 201 and "First-call tour" not in instructions


def test_tour_flag_adds_steps_with_the_users_shortcuts(server, service):
    service.settings.patch({"delivery": {"target": "telegram:555"}})
    tour = {"call": "⌃⌥Space", "mute": "⌃⌥M", "pause": "⌃⌥P"}
    status, _, instructions = open_call(server, service, {"tour": tour}, "req_tour")
    assert status == 201
    assert "First-call tour" in instructions
    for label in tour.values():
        assert label in instructions
    assert "skip" in instructions and "own words" in instructions


def test_tour_without_shortcuts_still_works(server, service):
    status, _, instructions = open_call(server, service, {"tour": {}}, "req_tour_bare")
    assert status == 201 and "First-call tour" in instructions and "mutes the mic" not in instructions


def test_tour_rejects_junk(server, service):
    assert open_call(server, service, {"tour": "yes"}, "req_bad_1")[0] == 400
    assert open_call(server, service, {"tour": {"call": "x" * 40}}, "req_bad_2")[0] == 400
    assert open_call(server, service, {"tour": {"other": "A"}}, "req_bad_3")[0] == 400


def test_tour_block_names_channels_when_set():
    text = P.tour_block(P.Names("Hermes", "Sam"), {"mute": "F5"}, "Telegram",
                        [{"label": "#work"}, {"label": "#family"}])
    assert "Sam's first call" in text and "F5" in text and "#work, #family" in text


def test_skip_button_tells_the_live_call(server, service):
    status, session, _ = open_call(server, service, {"tour": {}}, "req_skip")
    assert status == 201
    worker = service.workers[-1]
    worker.loop = None  # fake worker: record directly
    sent = []
    worker.speak_from_thread = lambda kind, content: sent.append((kind, content))
    status, body = http(server.base_url, "POST", f"/voice/interactions/{session['interaction_id']}/skip-tour",
                        {}, server.token)
    assert status == 200 and body["tour"] == "skipped"
    assert sent == [("session.thinking.append", P.TOUR_SKIPPED_NOTE)]
    assert http(server.base_url, "POST", "/voice/interactions/vi_nope/skip-tour", {}, server.token)[0] == 404
