"""Spoken acknowledgements (A), channel routing and new threads (B), continuity switch (C),
the routing model (one auxiliary call per handoff), and Suggest channels."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import time

import pytest

from fakes import SDP, http, wait_for
from speakeasy import channels, continuity, router, suggest, threads
from speakeasy import settings as S
from speakeasy.prompt import builder as P


def start_call(server, service, key="req_call_1"):
    status, session = http(server.base_url, "POST", "/voice/sessions", {"sdp": SDP}, server.token,
                           {"Idempotency-Key": key})
    assert status == 201, session
    return session, service.workers[-1]


def tasks(server):
    return http(server.base_url, "GET", "/voice/work/latest", token=server.token)[1]["tasks"]


def spoken(worker):
    return [c for k, _, c in worker.sent if k == "session.commentary.append"]


BUILD = {"target": "discord:111", "label": "#build", "topic": "building or changing software, apps, agents",
         "new_thread": False}
RESEARCH = {"target": "discord:222", "label": "#research", "topic": "reading up on a subject, comparing options",
            "new_thread": False}


# -- (A) spoken acknowledgement -------------------------------------------------------------------

def test_new_task_gets_one_short_spoken_acknowledgement(server, service, hermes):
    hermes.hold = True
    _, worker = start_call(server, service)
    worker.delegate("call_a", "Find me a dentist near the office")
    wait_for(lambda: spoken(worker))
    time.sleep(0.2)
    lines = spoken(worker)
    assert len(lines) == 1 and len(lines[0].split()) <= 6
    assert not any(word in lines[0].lower() for word in ("hermes", "backend", "codex"))


def test_no_double_speak_when_the_model_already_acknowledged(server, service, hermes):
    hermes.hold = True
    _, worker = start_call(server, service)
    worker.feed({"type": "session.input_transcript.delta", "delta": "Check the weather in Rome", "start_ms": 1, "end_ms": 2})
    worker.feed({"type": "session.output_transcript.delta", "delta": "Let me check."})
    worker.feed({"type": "session.delegation.created", "offset_ms": 5, "delegation": {"id": "call_w", "target": "client"}})
    wait_for(lambda: [t for t in tasks(server) if t.get("run_id")])
    time.sleep(0.2)
    assert spoken(worker) == []


def test_follow_up_acknowledgement_names_the_task(server, service, hermes):
    _, worker = start_call(server, service)
    worker.delegate("call_first", "Draft a packing list for the Rome trip")
    first = wait_for(lambda: [t for t in tasks(server) if t["status"] == "completed"])[0]
    worker.sent.clear()
    worker.delegate("call_second", "Add sunscreen to it", task_id=first["task_id"])
    line = wait_for(lambda: [c for c in spoken(worker) if c.startswith("Adding that to the")])[0]
    assert line.endswith("task.") and len(line.split()) <= 8


def test_both_transports_route_commentary_to_speech():
    from speakeasy import codex_transport
    source = open(codex_transport.__file__).read()
    assert '"thread/realtime/appendSpeech" if kind == "session.commentary.append"' in source
    from speakeasy import openai_live
    live = open(openai_live.__file__).read()
    assert '"type": kind' in live  # session.commentary.append goes out as-is: the live API speaks it


def test_progress_is_spoken_once_for_a_long_quiet_task(service, monkeypatch):
    import asyncio
    from speakeasy import calls
    from speakeasy.calls import BackendRun, Interaction
    from fakes import FakeLiveWorker
    worker = FakeLiveWorker(service.rt, Interaction("int_x", "sess_x"))
    backend = BackendRun("t1", 1, "idem_1", status="running")
    backend.started -= 25  # running for 25 s
    asyncio.run(worker.maybe_speak_progress(backend, "Pulling this week's events"))
    asyncio.run(worker.maybe_speak_progress(backend, "Verifying the injury report"))  # within 30 s: quiet
    lines = spoken(worker)
    assert len(lines) == 1 and "Pulling this week's events" in lines[0]
    backend.spoken_at -= calls.PROGRESS_EVERY_S + 1
    worker.fragments.append({"speaker": "user", "text": "hmm what else", "at": time.monotonic(), "start_ms": 0, "end_ms": 0})
    asyncio.run(worker.maybe_speak_progress(backend, "Almost there"))  # user spoke: stay quiet
    assert len(spoken(worker)) == 1
    fresh = BackendRun("t2", 1, "idem_2", status="running")
    asyncio.run(worker.maybe_speak_progress(fresh, "Just started"))  # under 20 s: quiet
    assert len(spoken(worker)) == 1


# -- routing model ---------------------------------------------------------------------------------

OPEN = [router.OpenTask("t1", "Plan the Rome trip itinerary", "running")]
TOPICS = [router.Topic("#build", BUILD["topic"]), router.Topic("#research", RESEARCH["topic"])]


def test_routing_model_splits_a_compound_request():
    call = lambda messages: json.dumps({"follow_up_task_id": None, "channel": None,
                                        "parts": ["Check the weather in Lisbon", "Book a table for two tonight"]})
    d = router.decide("Check the weather in Lisbon and book a table for two tonight", [], None, [], call)
    assert d.source == "model" and [p.request for p in d.parts] == ["Check the weather in Lisbon",
                                                                    "Book a table for two tonight"]


def test_routing_model_attaches_a_follow_up():
    call = lambda messages: '{"follow_up_task_id": "t1", "parts": [], "channel": null}'
    d = router.decide("Add a day in Florence", OPEN, None, [], call)
    assert d.parts[0].kind == "follow_up" and d.parts[0].task_id == "t1"


def test_routing_model_picks_a_channel_and_rejects_unknown_ones():
    pick = lambda label: (lambda messages: json.dumps({"follow_up_task_id": None, "parts": ["x"], "channel": label}))
    assert router.decide("Fix the login bug in my app", [], None, TOPICS, pick("build")).channel == "#build"
    assert router.decide("Fix the login bug in my app", [], None, TOPICS, pick("#nope")).channel is None


def test_routing_model_timeout_and_garbage_fall_back_to_rules():
    def slow(messages):
        time.sleep(1)
        return '{"follow_up_task_id": "t1", "parts": [], "channel": null}'
    started = time.monotonic()
    d = router.decide("What's the capital of Peru?", OPEN, None, [], slow, timeout=0.2)
    assert d.source == "fallback" and d.parts[0].kind == router.NEW and time.monotonic() - started < 0.8
    assert router.decide("x and y", [], None, [], lambda m: "not json").source == "fallback"
    assert router.decide("x and y", [], None, [], lambda m: '{"follow_up_task_id": "ghost"}').source == "fallback"
    boom = lambda m: (_ for _ in ()).throw(RuntimeError("no provider"))
    assert router.decide("x and y", [], None, [], boom).source == "fallback"


def test_routing_skips_the_model_when_nothing_to_decide():
    calls = []
    d = router.decide("What time is it in Tokyo?", [], None, [], lambda m: calls.append(m) or "{}")
    assert calls == [] and d.parts[0].kind == router.NEW


def test_routing_latency_is_stored_with_the_task(home, hermes):
    from speakeasy.service import VoiceService
    from fakes import FakeLiveWorker, FakeTransport
    workers = []
    svc = VoiceService(home, notifier=None, start_threads=False, codex_factory=FakeTransport,
                       openai_negotiate=lambda key, payload: {"session": {"id": "sess_fake"}, "transport": {"sdp": "v=0\r\n"}},
                       openai_worker=lambda rt, i: workers.append(FakeLiveWorker(rt, i)) or workers[-1],
                       route_call=lambda m: '{"follow_up_task_id": null, "parts": ["a", "b"], "channel": null}')
    svc.settings.patch({"voice": {"provider": "openai"}})
    try:
        svc.create_session({"sdp": SDP}, "req_lat")
        workers[-1].delegate("call_ab", "Check the weather and also book a table")
        wait_for(lambda: len(hermes.calls) == 2)
        keys = [r.idem_key for r in workers[-1].interaction.runs.values()]
        assert all(svc.store.timings(k).get("routing_ms") is not None for k in keys)
        assert any("split into separate tasks" in c for _, _, c in workers[-1].sent)
    finally:
        svc.close()


# -- (B) channels ---------------------------------------------------------------------------------

def _settings(**delivery):
    return S.validate({"delivery": {"target": "telegram:555", "channels": [BUILD, RESEARCH], **delivery}})


def test_explicit_channel_naming_wins_and_ambiguity_asks():
    s = _settings()
    assert channels.explicit("start this in build: a menu bar timer", s).channel.label == "#build"
    assert channels.explicit("put it in #research please", s).channel.label == "#research"
    both = channels.explicit("post it in #build and #research", s, P.clarify_channel)
    assert both.clarify and "#build" in both.clarify and "#research" in both.clarify
    unknown = channels.explicit("put it in #cooking", s, P.clarify_channel)
    assert unknown.clarify and unknown.channel is None
    assert channels.explicit("put it in writing for me", s) is None  # ordinary phrase, not a channel


def test_topical_pick_or_default():
    s = _settings()
    assert channels.resolve(s, "Telegram", "#build").channel.target == "discord:111"
    fallback = channels.resolve(s, "Telegram", None)
    assert fallback.channel.default and fallback.channel.target == "telegram:555"


def test_legacy_settings_migrate():
    s = S.validate({"delivery": {"target": "discord:123", "new_thread_per_task": True}})
    assert s["delivery"] == {"target": "discord:123", "new_thread": True, "channels": []}


def test_channel_settings_are_validated(server):
    bad = [{"target": "rm -rf", "label": "#x"}, {"target": "discord:1", "label": "{oops}"},
           {"target": "discord:1", "label": "#a", "topic": "x" * 200}]
    for channel in bad:
        status, _ = http(server.base_url, "PATCH", "/voice/settings", {"delivery": {"channels": [channel]}}, server.token)
        assert status == 400
    dup = [BUILD, dict(BUILD, label="#other")]
    assert http(server.base_url, "PATCH", "/voice/settings", {"delivery": {"channels": dup}}, server.token)[0] == 400
    status, body = http(server.base_url, "PATCH", "/voice/settings", {"delivery": {"channels": [BUILD]}}, server.token)
    assert status == 200 and body["settings"]["delivery"]["channels"][0]["label"] == "#build"


def test_named_channel_runs_here_and_posts_the_result_there(server, service, hermes):
    service.settings.patch({"delivery": {"target": "telegram:555", "channels": [BUILD]}})
    posted = []
    service.notices.post = lambda key, text, limit=600, target=None: posted.append((key, target)) or True
    _, worker = start_call(server, service)
    worker.delegate("call_b", "Start this in build: add a dark mode toggle to the timer app")
    wait_for(lambda: [t for t in tasks(server) if t["status"] == "completed"])
    wait_for(lambda: posted)
    assert posted[0][1] == "discord:111"
    assert any("#build" in line for line in spoken(worker))
    assert "#build" in hermes.calls[0]["input"]


def test_unknown_channel_does_not_start_and_asks(server, service, hermes):
    service.settings.patch({"delivery": {"target": "telegram:555", "channels": [BUILD]}})
    _, worker = start_call(server, service)
    worker.delegate("call_c", "put it in #cooking: find a pasta recipe")
    wait_for(lambda: spoken(worker))
    time.sleep(0.2)
    assert hermes.calls == [] and "channel" in spoken(worker)[0]


class FakeThreads:
    def __init__(self, answer="Voice: x\nThe timer now has dark mode.", fail=False):
        self.answer, self.fail, self.opened = answer, fail, []

    def available(self, target):
        return True

    def open(self, target, *, message, title, delivery_id):
        if self.fail:
            raise threads.ThreadError("no")
        self.opened.append((target, message, title))
        return threads.Opened("speakeasy-x", "999", "discord")

    def wait(self, opened, on_session):
        on_session("thread_session_1")
        return self.answer


def test_new_thread_channel_runs_the_task_in_a_thread(server, service, hermes):
    runner = FakeThreads()
    service.rt.threads = runner
    service.settings.patch({"delivery": {"target": "telegram:555", "channels": [dict(BUILD, new_thread=True)]}})
    _, worker = start_call(server, service)
    worker.delegate("call_t", "Start this in build: add dark mode to the timer app")
    done = wait_for(lambda: [t for t in tasks(server) if t["status"] == "completed"])[0]
    assert runner.opened and runner.opened[0][0] == "discord:111" and hermes.calls == []
    assert "Started that in a new #build thread." in spoken(worker)
    assert "dark mode" in done["result"]["full"] and not done["result"]["full"].startswith("Voice:")
    key = next(iter(worker.interaction.runs.values())).idem_key
    assert service.store.continued_for(key)["session_id"] == "thread_session_1"  # follow-ups go there


def test_thread_failure_falls_back_to_posting(server, service, hermes):
    service.rt.threads = FakeThreads(fail=True)
    service.settings.patch({"delivery": {"target": "telegram:555", "channels": [dict(BUILD, new_thread=True)]}})
    _, worker = start_call(server, service)
    worker.delegate("call_f", "Start this in build: add dark mode to the timer app")
    wait_for(lambda: [t for t in tasks(server) if t["status"] == "completed"])
    assert len(hermes.calls) == 1


def test_rules_list_the_opted_in_channels():
    text = P.rules_text(P.Names.from_settings(S.validate({})), "Telegram", [BUILD, dict(RESEARCH, new_thread=True)])
    assert "#build (building or changing software" in text and "own new thread" in text
    assert "{" not in text.split("# Backchannel")[0]


# -- threads: routes, secret, state DB ------------------------------------------------------------

def _state_db(path, rows):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, chat_type TEXT, thread_id TEXT, "
               "origin_json TEXT, title TEXT, started_at REAL, ended_at REAL)")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
               "content TEXT, tool_calls TEXT, timestamp REAL)")
    for sid, source, chat_type, thread_id, origin in rows:
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,NULL)",
                   (sid, source, chat_type, thread_id, json.dumps(origin), "t", time.time()))
    db.commit()
    return db


def test_routes_are_generated_per_channel_with_a_private_secret(tmp_path):
    (tmp_path / "webhook_subscriptions.json").write_text(json.dumps({"mine": {"secret": "keep"}}))
    _state_db(tmp_path / "state.db", [("s1", "discord", "group", None,
                                       {"platform": "discord", "chat_id": "111", "chat_type": "group",
                                        "user_id": "42", "user_name": "sam"})]).close()
    made = threads.sync_routes(tmp_path, [dict(BUILD, new_thread=True), RESEARCH], supported=True)
    subs = json.loads((tmp_path / "webhook_subscriptions.json").read_text())
    route = subs[made["discord:111"]]
    assert set(made) == {"discord:111"} and subs["mine"] == {"secret": "keep"}
    assert route["source_new_thread"] is True and route["source_chat_id"] == "111" and route["source_user_id"] == "42"
    secret_file = tmp_path / "speakeasy" / "webhook_secret"
    assert stat.S_IMODE(os.stat(secret_file).st_mode) == 0o600 and route["secret"] == secret_file.read_text()
    threads.sync_routes(tmp_path, [], supported=True)  # channel removed: its route goes, others stay
    assert json.loads((tmp_path / "webhook_subscriptions.json").read_text()) == {"mine": {"secret": "keep"}}


def test_thread_answer_is_read_from_the_state_db(tmp_path):
    db = _state_db(tmp_path / "state.db", [("s_thread", "discord", "thread", "999", {"platform": "discord"})])
    opened = threads.Opened("r", "999", "discord")
    seen = []
    assert threads.wait_for_answer(tmp_path / "state.db", opened, 0, sleep=lambda s: None, on_session=seen.append) is None
    db.execute("INSERT INTO messages (session_id, role, content, tool_calls, timestamp) VALUES ('s_thread','assistant','Done here',NULL,1)")
    db.commit()
    assert threads.wait_for_answer(tmp_path / "state.db", opened, 0, sleep=lambda s: None) == "Done here"
    assert seen == ["s_thread"]


def test_thread_capability_needs_the_webhook_platform(tmp_path):
    assert threads.capability(tmp_path, True)["supported"] is False
    (tmp_path / "config.yaml").write_text("platforms:\n  webhook:\n    enabled: true\n    extra:\n      port: 8650\n")
    assert threads.capability(tmp_path, True)["supported"] is True
    assert threads.webhook_base(tmp_path) == "http://127.0.0.1:8650"
    assert threads.capability(tmp_path, False)["supported"] is False


# -- (C) continuity ---------------------------------------------------------------------------------

def test_thread_sessions_opened_for_voice_are_continuation_candidates(tmp_path):
    db = _state_db(tmp_path / "state.db", [("s_thr", "discord", "thread", "999",
                                            {"platform": "discord", "chat_id": "999", "chat_type": "thread",
                                             "chat_name": "Home / #build / Timer dark mode", "user_id": "42",
                                             "thread_id": "999", "parent_chat_id": "111"})])
    db.execute("INSERT INTO messages (session_id, role, content, tool_calls, timestamp) VALUES ('s_thr','assistant','ok',NULL,?)",
               (time.time(),))
    db.commit()
    assert "webhook" in continuity.EXCLUDED_SOURCES  # webhook-only runs stay out; threads carry the chat source
    assert [c.session_id for c in continuity.recent_conversations(tmp_path / "state.db")] == ["s_thr"]


def test_continuity_off_stops_matching_end_to_end(server, service, hermes, monkeypatch):
    matched = []
    conv = continuity.Conversation("s_trip", "telegram", "1", "group", "", "42", "", "Travel",
                                   "Lisbon trip planning", time.time())
    monkeypatch.setattr(continuity, "recent_conversations", lambda db, **kw: matched.append(1) or [conv])
    monkeypatch.setattr(continuity, "match", lambda request, convs: None)
    status, _ = http(server.base_url, "PATCH", "/voice/settings", {"continuity": {"enabled": False}}, server.token)
    assert status == 200
    _, worker = start_call(server, service)
    worker.delegate("call_off", "In the Lisbon trip planning chat, also book a museum")
    wait_for(lambda: len(hermes.calls) == 1)
    assert matched == []  # never even looked
    http(server.base_url, "PATCH", "/voice/settings", {"continuity": {"enabled": True}}, server.token)
    worker.delegate("call_on", "In the Lisbon trip planning chat, also book dinner")
    wait_for(lambda: len(hermes.calls) == 2)
    assert matched == [1]


def test_onboarding_can_turn_continuity_off(server):
    status, body = http(server.base_url, "POST", "/voice/onboarding", {"continuity_enabled": False}, server.token)
    assert status == 200 and body["settings"]["continuity"]["enabled"] is False and body["continuity_enabled"] is False
    assert http(server.base_url, "POST", "/voice/onboarding", {"continuity_enabled": "no"}, server.token)[0] == 400


# -- Suggest channels ------------------------------------------------------------------------------

def _destinations(home):
    (home / "gateway_state.json").write_text(json.dumps({"platforms": {"discord": {"state": "connected"}}}))
    (home / "channel_directory.json").write_text(json.dumps({"platforms": {"discord": [
        {"id": "111", "name": "build", "guild": "Home", "type": "channel"},
        {"id": "222", "name": "research", "guild": "Home", "type": "channel"}]}}))


def test_suggest_channels_validates_and_never_saves(server, service, home):
    _destinations(home)
    reply = json.dumps([
        {"target": "discord:111", "label": "#build", "topic": "coding and apps", "new_thread": True},
        {"target": "discord:999", "label": "#ghost", "topic": "not offered"},
        {"target": "discord:222", "label": "{bad}", "topic": "x"},
        {"target": "discord:222", "label": "#research", "topic": "reading up on things"}])
    service._suggest_run = lambda prompt, idem: ("completed", "Here you go:\n```json\n" + reply + "\n```")
    status, body = http(server.base_url, "POST", "/voice/destinations/suggest", {}, server.token)
    assert status == 200
    assert [s["target"] for s in body["suggestions"]] == ["discord:111", "discord:222"]
    assert service.settings.get()["delivery"]["channels"] == []


def test_suggest_prompt_lists_only_labels_and_targets(home):
    _destinations(home)
    from speakeasy import delivery as D
    seen = {}
    with pytest.raises(suggest.SuggestError):
        suggest.suggest(lambda prompt, idem: seen.update(prompt=prompt) or ("completed", "[]"),
                        D.flat_chats(D.destinations(home)), False)
    assert "discord:111" in seen["prompt"] and "Home / build" in seen["prompt"]


def test_suggest_failure_is_plain_text(server, service, home):
    _destinations(home)
    service._suggest_run = lambda prompt, idem: ("failed", "")
    status, body = http(server.base_url, "POST", "/voice/destinations/suggest", {}, server.token)
    assert status == 502 and "suggest" in body["error"].lower()
    service._suggest_run = lambda prompt, idem: ("completed", "I think #build is nice")
    assert http(server.base_url, "POST", "/voice/destinations/suggest", {}, server.token)[0] == 502


def test_status_shows_routing_model_and_tailscale(server):
    status, body = http(server.base_url, "GET", "/voice/status", token=server.token)
    assert status == 200 and body["routing_model"] and "speakeasy_router" in body["routing_hint"]
    assert body["advertised_url"] == "" and "tailscale_name" in body
