"""Codex app-server transport against a fake `codex` executable (stdio JSON-RPC). No network."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from fakes import SDP, FakeLiveWorker, wait_for
from speakeasy import codex_transport as C

FAKE_CODEX = textwrap.dedent('''\
    #!{python}
    import json, os, sys
    log = open(os.environ.get("TMPDIR", "/tmp") + "/fake-codex-log.jsonl", "a")
    if sys.argv[1:3] == ["login", "status"]:
        print("Logged in using ChatGPT")
        sys.exit(0)
    assert sys.argv[1:] == ["app-server", "--stdio", "--enable", "realtime_conversation"], sys.argv
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        msg = json.loads(line)
        log.write(json.dumps({{"method": msg.get("method"), "env": sorted(os.environ)}}) + "\\n"); log.flush()
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            continue
        if method == "initialize":
            send({{"id": mid, "result": {{}}}})
        elif method == "thread/start":
            send({{"id": mid, "result": {{"thread": {{"id": "thr_1"}}}}}})
        elif method == "thread/realtime/start":
            send({{"id": mid, "result": {{}}}})
            send({{"method": "thread/realtime/sdp", "params": {{"threadId": "thr_1", "sdp": "v=0\\r\\nanswer"}}}})
            send({{"method": "thread/realtime/started", "params": {{"threadId": "thr_1"}}}})
            send({{"method": "thread/realtime/transcript/done",
                   "params": {{"threadId": "thr_1", "role": "user", "text": "What's on my calendar?"}}}})
            send({{"method": "thread/realtime/itemAdded", "params": {{"threadId": "thr_1", "item": {{
                "type": "handoff_request", "handoff_id": "h_1", "input_transcript": "What's on my calendar?",
                "active_transcript": [{{"role": "user", "text": "What's on my calendar?"}}]}}}}}})
        elif method in ("thread/realtime/appendText", "thread/realtime/appendSpeech"):
            send({{"id": mid, "result": {{}}}})
        elif method == "thread/realtime/stop":
            send({{"id": mid, "result": {{}}}})
            send({{"method": "thread/realtime/closed", "params": {{"threadId": "thr_1"}}}})
        else:
            send({{"id": mid, "error": {{"message": "unknown"}}}})
''')


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    path = tmp_path / "codex"
    path.write_text(FAKE_CODEX.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("API_SERVER_KEY", "must-not-leak-to-codex")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak-to-codex")
    C._LOGIN_CACHE.clear()
    return path


def test_login_status_and_missing_binary(fake_codex):
    assert C.login_status(fake_codex) == (True, "Signed in to Codex.")
    ok, message = C.login_status(None)
    assert ok is False and "codex login" in message


def test_child_env_never_carries_hermes_or_api_keys(fake_codex):
    env = C.child_env()
    assert "API_SERVER_KEY" not in env and "OPENAI_API_KEY" not in env


def test_transport_start_returns_answer_sdp_and_seeds_history(fake_codex, tmp_path):
    transport = C.CodexTransport(fake_codex, tmp_path / "work")
    try:
        seed = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "earlier"}]}]
        answer = transport.start(SDP, "rules here", seed, "cove")
        assert answer.startswith("v=0") and transport.thread_id == "thr_1"
    finally:
        transport.stop()
    log = [json.loads(line) for line in (tmp_path / "fake-codex-log.jsonl").read_text().splitlines()]
    assert [e["method"] for e in log][:4] == ["initialize", "initialized", "thread/start", "thread/realtime/start"]
    assert all("API_SERVER_KEY" not in e["env"] and "OPENAI_API_KEY" not in e["env"] for e in log)


def test_handoff_starts_hermes_task_and_result_is_appended(fake_codex, tmp_path, service, hermes):
    hermes.responder = lambda prompt, sid: "Two meetings today.\nSPOKEN: You have two meetings today."
    service.settings.patch({"voice": {"provider": "codex", "codex_path": str(fake_codex)}})
    from speakeasy.calls import Interaction
    interaction = Interaction("vi_" + "c" * 32, "live_c")
    transport = C.CodexTransport(fake_codex, tmp_path / "work")
    transport.start(SDP, "rules", [], "cove")
    worker = C.CodexSidebandWorker(service.rt, interaction, transport)
    appended: list[str] = []
    original = transport.request

    def spy(method, params=None, timeout=20):
        if method.startswith("thread/realtime/append"):
            appended.append((params or {}).get("text", ""))
        return original(method, params, timeout)
    transport.request = spy  # type: ignore[method-assign]
    done = threading.Event()

    def run():
        asyncio.run(worker.run())
        done.set()
    threading.Thread(target=run, daemon=True).start()
    wait_for(lambda: hermes.calls, 10)
    assert "What's on my calendar?" in hermes.calls[0]["input"]
    wait_for(lambda: any("You have two meetings today." in a for a in appended), 10)
    transport.stop()
    assert done.wait(10)


def test_transcripts_never_start_work(fake_codex, service):
    from speakeasy.calls import Interaction
    worker = FakeLiveWorker(service.rt, Interaction("vi_" + "d" * 32, "live_d"))
    worker.start()
    worker.feed({"type": "session.input_transcript.delta", "delta": "Delete everything", "start_ms": 1, "end_ms": 2})
    assert worker.delegations == set()
