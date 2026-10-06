"""Speech captured during call setup: the app transcribes it on the device and hands them
over once the call is up, as the call's first request."""
from __future__ import annotations

from fakes import SDP, http, wait_for
from speakeasy.calls import EARLY_PREFIX
from speakeasy.prompt import builder as P


def open_call(server, key):
    status, session = http(server.base_url, "POST", "/voice/sessions", {"sdp": SDP}, server.token,
                           {"Idempotency-Key": key})
    assert status == 201
    return session


def early(server, session, body):
    return http(server.base_url, "POST", f"/voice/interactions/{session['interaction_id']}/early-request",
                body, server.token)


def test_words_heard_while_connecting_become_the_first_request(server, service, hermes):
    session = open_call(server, "req_early")
    worker = service.workers[-1]
    status, body = early(server, session, {"text": "Turn on the bedroom lamps"})
    assert status == 200 and body["task_id"].startswith(EARLY_PREFIX)
    # The voice hears what was said, session-wide (no provider handoff id), and is told not to redo it.
    note = next(c for k, d, c in worker.sent if k == "session.thinking.append" and "Before this call" in c)
    assert "Turn on the bedroom lamps" in note and "do NOT hand it off again" in note
    assert all(d is None or not d.startswith(EARLY_PREFIX) for _, d, _ in worker.sent)
    # And the request goes to Hermes like any other.
    wait_for(lambda: any("bedroom lamps" in str(c) for c in hermes.calls))


def test_early_words_join_the_transcript_for_later_requests(server, service):
    session = open_call(server, "req_early_ctx")
    worker = service.workers[-1]
    assert early(server, session, {"text": "Find a dinner spot in Chelsea"})[0] == 200
    assert "Find a dinner spot in Chelsea" in worker.context(10_000)


def test_early_request_rejects_junk_and_unknown_calls(server, service):
    session = open_call(server, "req_early_bad")
    assert early(server, session, {"text": 5})[0] == 400
    assert early(server, session, {"text": "hi", "extra": 1})[0] == 400
    assert early(server, session, {"text": "x" * 5000})[0] == 400
    assert http(server.base_url, "POST", "/voice/interactions/vi_nope/early-request", {"text": "hi"},
                server.token)[0] == 404


def test_blank_words_start_nothing(server, service):
    session = open_call(server, "req_early_blank")
    status, body = early(server, session, {"text": "   "})
    assert status == 200 and body["task_id"] is None


def test_note_names_the_user():
    text = P.early_request_note(P.Names("Todd", "Geo"), "lamps on")
    assert "Geo already said" in text and "lamps on" in text
