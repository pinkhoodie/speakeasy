"""Ambient listening (docs/ambient-listening-plan.md): settings, transcript dedup, compression
cadence, Mode A/B wake split, and session seeding — over real HTTP with no model calls."""
from __future__ import annotations

import json

from fakes import SDP, http

TS = "2026-10-07T09:30:00Z"


def _post(server, path, body, headers=None):
    return http(server.base_url, "POST", path, body, server.token, headers)


def _transcript(server, text, ts=TS):
    return _post(server, "/voice/listening/transcript", {"text": text, "ts": ts})


def test_listening_defaults_and_state(server, service):
    status, body = http(server.base_url, "GET", "/voice/listening/state", token=server.token)
    assert status == 200
    assert body == {"enabled": False, "memory_chars": 0, "last_activity_at": None}
    listening = service.settings.get()["listening"]
    assert listening["enabled"] is False and listening["wake_phrases"] == ["hey speakeasy"]
    assert listening["hot_context_words"] == 2000 and listening["summary_every"] == 3
    assert listening["compress_target_words"] == 600 and listening["mode_b_max_tokens"] == 120


def test_listening_settings_patch_and_validation(server):
    status, body = http(server.base_url, "PATCH", "/voice/settings",
                        {"listening": {"enabled": True, "wake_variants": ["hey speakeazy"]}}, server.token)
    assert status == 200
    listening = body["settings"]["listening"]
    assert listening["enabled"] is True and listening["wake_variants"] == ["hey speakeazy"]
    assert listening["wake_phrases"] == ["hey speakeasy"]  # untouched keys keep their defaults
    # unknown listening.* key, bad shapes and ranges are rejected like every other section
    assert _patch(server, {"listening": {"made_up": 1}})[0] == 400
    assert "listening.made_up" in _patch(server, {"listening": {"made_up": 1}})[1]["error"]
    assert _patch(server, {"listening": {"wake_phrases": "hey speakeasy"}})[0] == 400
    assert _patch(server, {"listening": {"wake_phrases": []}})[0] == 400
    assert _patch(server, {"listening": {"debounce_seconds": "soon"}})[0] == 400
    assert _patch(server, {"listening": {"summary_every": 0}})[0] == 400
    assert _patch(server, {"listening": {"enabled": "yes"}})[0] == 400
    assert _patch(server, {"listening": "on"})[0] == 400


def _patch(server, body):
    return http(server.base_url, "PATCH", "/voice/settings", body, server.token)


def test_transcript_stores_dedups_and_marks_activity(server):
    assert _transcript(server, "morning sync moved to ten")[1] == {"stored": True}
    assert _transcript(server, "the build is red again")[1] == {"stored": True}
    assert _transcript(server, "i'll ask design about the icons")[1] == {"stored": True}
    # exact duplicate of the tail: dropped
    assert _transcript(server, "  i'll ask design about the icons ")[1] == {"stored": False}
    # progressive extension (STT re-emitted the same words plus a little): dropped while it is
    # still just the tail repeated; a genuinely new segment stores
    assert _transcript(server, "checking the weather")[1] == {"stored": True}
    assert _transcript(server, "checking the weather in cairo")[1] == {"stored": False}
    assert _transcript(server, "pizza friday is back on")[1] == {"stored": True}
    status, body = http(server.base_url, "GET", "/voice/listening/state", token=server.token)
    assert status == 200 and body["last_activity_at"] and body["last_activity_at"] > 0
    status, memory = http(server.base_url, "GET", "/voice/listening/memory", token=server.token)
    assert status == 200
    assert [seg["text"] for seg in memory["hot"]] == ["morning sync moved to ten", "the build is red again",
                                                      "i'll ask design about the icons", "checking the weather",
                                                      "pizza friday is back on"]
    assert memory["compressed"] == "" and memory["compression_count"] == 0 and memory["summary"] is None


def test_transcript_validation(server):
    assert _post(server, "/voice/listening/transcript", {"text": "hi"})[0] == 400          # no ts
    assert _post(server, "/voice/listening/transcript", {"ts": TS})[0] == 400              # no text
    assert _post(server, "/voice/listening/transcript", {"text": "hi", "ts": TS, "x": 1})[0] == 400
    assert _post(server, "/voice/listening/transcript", {"text": "hi", "ts": "today"})[0] == 400
    assert _post(server, "/voice/listening/transcript", {"text": "hi", "ts": 123})[0] == 400
    assert _post(server, "/voice/listening/transcript", {"text": "   ", "ts": TS})[0] == 400
    assert _post(server, "/voice/listening/transcript", {"text": "word " * 500, "ts": TS})[0] == 400


def _install_llm(service, replies):
    """Route each fake completion by what it is being asked for."""
    calls = []

    def llm(prompt, max_tokens=300):
        calls.append({"prompt": prompt, "max_tokens": max_tokens})
        for marker, reply in replies.items():
            if marker in prompt:
                return reply
        return None

    service._listening_call = llm
    return calls


def test_compress_cycle_and_summary_cadence(server, service):
    calls = _install_llm(service, {
        "conversation compression engine": "Dense compressed memory of the morning.",
        "session summary": "- Key topics: standup moved to ten",
        '"title"': '{"title": "Standup reshuffle", "subtitle": "The team moved the standup."}',
    })
    for _ in range(2):
        body = _post(server, "/voice/listening/compress", {})[1]
        assert body["summary"] is None and body["title"] is None  # summary_every=3: not yet
    third = _post(server, "/voice/listening/compress", {})[1]
    assert third["compression_count"] == 3
    assert third["compressed"] == "Dense compressed memory of the morning."
    assert third["summary"] == "- Key topics: standup moved to ten"
    assert third["title"] == "Standup reshuffle" and third["subtitle"] == "The team moved the standup."
    # every compression touched the model once; the third cycle also wrote summary and title
    assert len(calls) == 5
    assert calls[0]["prompt"].startswith("You are a conversation compression engine")
    tokens = {c["max_tokens"] for c in calls}
    assert tokens == {256, 1024}
    status, memory = http(server.base_url, "GET", "/voice/listening/memory", token=server.token)
    assert memory["compression_count"] == 3 and memory["title"] == "Standup reshuffle"
    assert memory["summary"] == "- Key topics: standup moved to ten"
    # the 4th cycle is off-cadence again: the response carries no summary, memory keeps the old one
    fourth = _post(server, "/voice/listening/compress", {})[1]
    assert fourth["compression_count"] == 4 and fourth["summary"] is None and fourth["title"] is None
    _, memory = http(server.base_url, "GET", "/voice/listening/memory", token=server.token)
    assert memory["summary"] == "- Key topics: standup moved to ten"
    _, state = http(server.base_url, "GET", "/voice/listening/state", token=server.token)
    assert state["memory_chars"] == len("Dense compressed memory of the morning.")


def test_compress_without_a_model_fails_and_changes_nothing(server, service):
    service._listening_call = lambda prompt, max_tokens: None
    assert _post(server, "/voice/listening/compress", {})[0] == 502
    _, memory = http(server.base_url, "GET", "/voice/listening/memory", token=server.token)
    assert memory["compression_count"] == 0 and memory["compressed"] == ""


def test_title_model_output_that_is_not_json_is_tolerated(server, service):
    _install_llm(service, {
        "conversation compression engine": "Some memory.",
        "session summary": "Summary text.",
        '"title"': 'Sure: ```json\n{"title": "Quiet morning", "subtitle": "Not much happened."}\n```',
    })
    body: dict = {}
    for _ in range(3):
        _, body = _post(server, "/voice/listening/compress", {})
    assert body["title"] == "Quiet morning" and body["subtitle"] == "Not much happened."


def test_wake_mode_split(server, service):
    _transcript(server, "the build is red again")
    _transcript(server, "design review at two")
    # Mode B: insight — a cheap completion answers inline, no session
    calls = _install_llm(service, {"wake phrase": "Sounds like the build is the thing to poke at."})
    status, body = _post(server, "/voice/listening/wake", {"mode": "insight", "context_tail": "build talk"})
    assert status == 200 and body == {"answer": "Sounds like the build is the thing to poke at."}
    assert "the build is red again" in calls[0]["prompt"] and calls[0]["max_tokens"] == 120
    # Mode B with a model that says nothing: the graceful line, never silence
    service._listening_call = lambda prompt, max_tokens: None
    assert _post(server, "/voice/listening/wake", {"mode": "insight"})[1]["answer"]

    # Mode A: question — no model call; the Mac gets a seed and opens the session itself
    service._listening_call = lambda prompt, max_tokens: (_ for _ in ()).throw(AssertionError("Mode A must not call a model"))
    status, body = _post(server, "/voice/listening/wake",
                         {"mode": "question", "question": "what's on my plate today?"})
    assert status == 200 and body["open_session"] is True
    seed = body["seed"]["ambient"]
    assert seed["question"] == "what's on my plate today?"
    assert "design review at two" in seed["hot_context"]
    assert seed["compressed"] == "" and seed["summary"] is None

    # wake validation
    assert _post(server, "/voice/listening/wake", {"mode": "other"})[0] == 400
    assert _post(server, "/voice/listening/wake", {"mode": "question"})[0] == 400  # no question
    assert _post(server, "/voice/listening/wake", {"mode": "question", "question": 9})[0] == 400
    assert _post(server, "/voice/listening/wake", {"mode": "insight", "junk": 1})[0] == 400
    assert _post(server, "/voice/listening/wake", {})[0] == 400


def test_seeded_session_knows_the_room(server, service):
    _transcript(server, "design review at two")
    service._listening_call = lambda prompt, max_tokens: "Compressed room memory."
    _post(server, "/voice/listening/compress", {})  # give the room a compressed memory first
    _, wake = _post(server, "/voice/listening/wake", {"mode": "question", "question": "what's on my plate?"})
    seen = {}

    def negotiate(key, payload):
        seen["instructions"] = payload["session"]["instructions"]
        return {"session": {"id": "sess_seed"}, "transport": {"sdp": "v=0\r\n"}}

    service._openai_negotiate = negotiate
    status, session = _post(server, "/voice/sessions", {"sdp": SDP, "ambient": wake["seed"]["ambient"]},
                            headers={"Idempotency-Key": "req_seed_1"})
    assert status == 201
    instructions = seen["instructions"]
    assert "# You were already listening" in instructions
    assert "Answer THIS first" in instructions and "what's on my plate?" in instructions
    assert "design review at two" in instructions and "Compressed room memory." in instructions
    # a plain session (no ambient) never grows the block
    seen.clear()
    status, _ = http(server.base_url, "POST", "/voice/sessions", {"sdp": SDP}, server.token,
                     {"Idempotency-Key": "req_plain_1"})
    assert status == 201 and "# You were already listening" not in seen["instructions"]


def test_seeded_session_validates_ambient(server):
    ok = {"mode": "question", "question": "hi", "hot_context": "words", "compressed": "c", "summary": None}
    assert _post(server, "/voice/sessions", {"sdp": SDP, "ambient": "yes"})[0] == 400
    assert _post(server, "/voice/sessions", {"sdp": SDP, "ambient": {**ok, "junk": 1}})[0] == 400
    assert _post(server, "/voice/sessions", {"sdp": SDP, "ambient": {**ok, "mode": "psychic"}})[0] == 400
    assert _post(server, "/voice/sessions", {"sdp": SDP, "ambient": {**ok, "question": 5}})[0] == 400


def test_store_bounds_the_transcript(service, server):
    for i in range(2005):
        assert service.store.ambient_append(TS, f"unique segment number {i} about the build") is True
    with service.store._lock:
        rows = service.store._db.execute("SELECT COUNT(*) FROM ambient_transcript").fetchone()
    assert rows[0] == 2000
