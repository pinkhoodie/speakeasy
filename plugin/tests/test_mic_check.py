"""The app reports when a call's mic wasn't getting through and it reopened the connection."""
from __future__ import annotations

import logging

from fakes import SDP, http


def open_call(server, key):
    status, session = http(server.base_url, "POST", "/voice/sessions", {"sdp": SDP}, server.token,
                           {"Idempotency-Key": key})
    assert status == 201
    return session


def report(server, session, body):
    return http(server.base_url, "POST", f"/voice/interactions/{session['interaction_id']}/mic-check",
                body, server.token)


def test_dead_mic_repair_is_logged_with_its_reason(server, caplog):
    session = open_call(server, "req_mic")
    with caplog.at_level(logging.WARNING, logger="speakeasy.service"):
        status, body = report(server, session, {"note": "repair 1: no sound from the mic"})
    assert status == 200 and body["logged"] is True
    assert any("mic check" in r.getMessage() and "no sound from the mic" in r.getMessage() for r in caplog.records)


def test_mic_report_only_takes_a_short_note(server):
    session = open_call(server, "req_mic_bad")
    assert report(server, session, {"note": "x" * 401})[0] == 400
    assert report(server, session, {"note": "ok", "audio": "..."})[0] == 400


def test_mic_report_needs_a_real_call(server):
    status, _ = http(server.base_url, "POST", "/voice/interactions/vi_" + "0" * 32 + "/mic-check",
                     {"note": "repair 1"}, server.token)
    assert status == 404
