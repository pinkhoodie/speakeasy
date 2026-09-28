"""CodexBackend against a fake `codex app-server` child (stdio JSON-RPC lines). No network."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from fakes import wait_for
from speakeasy.backends import BackendError, TaskBackend
from speakeasy.backends.codex import CodexBackend, parse_diff_stats
from speakeasy.text import ID_RE, interim_progress

DIFF = ("diff --git a/web/app.js b/web/app.js\n--- a/web/app.js\n+++ b/web/app.js\n@@ -1,2 +1,2 @@\n"
        "-old line\n+new line\n+another\n"
        "diff --git a/notes.txt b/notes.txt\nnew file mode 100644\n--- /dev/null\n+++ b/notes.txt\n@@ -0,0 +1 @@\n+hi\n")

FAKE_APP_SERVER = textwrap.dedent('''\
    import json, os, sys, time
    log = open(os.environ["FAKE_LOG"], "a")
    ws = os.environ["FAKE_WS"]
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
    def note(**kw):
        send({"method": kw.pop("m"), "params": kw})
    def agent(tid, turn, iid, text, phase):
        note(m="item/completed", threadId=tid, turnId=turn, completedAtMs=0,
             item={"type": "agentMessage", "id": iid, "text": text, "phase": phase, "memoryCitation": None})
    def done(tid, turn, status, error=None):
        note(m="turn/completed", threadId=tid, turn={"id": turn, "items": [], "itemsView": "full",
             "status": status, "error": error, "startedAt": 0, "completedAt": 0, "durationMs": 1})
    turns, waiting = 0, {}
    assert sys.argv[1:] == ["app-server", "--stdio"], sys.argv
    for line in sys.stdin:
        msg = json.loads(line)
        log.write(json.dumps(msg) + "\\n"); log.flush()
        method, mid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None:          # a response to one of our server requests
            kind = waiting.pop(mid, None)
            if kind:
                tid, turn = kind
                decision = (msg.get("result") or {}).get("decision") or json.dumps(msg.get("result"))
                agent(tid, turn, "m_final", "decision=" + str(decision), "final_answer")
                done(tid, turn, "completed")
            continue
        if mid is None:
            continue
        if method == "initialize":
            send({"id": mid, "result": {"userAgent": "fake", "codexHome": "/x", "platformFamily": "unix",
                                        "platformOs": "macos"}})
        elif method in ("thread/start", "thread/resume"):
            tid = p.get("threadId") or "thr_new"
            send({"id": mid, "result": {"thread": {"id": tid, "turns": []}, "model": "m", "cwd": p.get("cwd")}})
        elif method == "turn/start":
            turns += 1
            tid, turn = p["threadId"], "turn_%d" % turns
            prompt = p["input"][0]["text"]
            send({"id": mid, "result": {"turn": {"id": turn, "items": [], "status": "inProgress", "error": None}}})
            note(m="turn/started", threadId=tid, turn={"id": turn, "items": [], "status": "inProgress"})
            if prompt == "hello":
                agent(tid, turn, "m1", "STATUS: Checking the tests\\nDETAIL: Looking at the test suite.", "commentary")
                note(m="item/started", threadId=tid, turnId=turn, startedAtMs=0, item={
                    "type": "commandExecution", "id": "c1", "command": "npm test --token=abc", "cwd": ws + "/web",
                    "status": "inProgress", "commandActions": [{"type": "unknown", "command": "npm test"}]})
                note(m="item/started", threadId=tid, turnId=turn, startedAtMs=0, item={
                    "type": "commandExecution", "id": "c2", "command": "/bin/zsh -lc 'cargo build'", "cwd": ws,
                    "status": "inProgress", "commandActions": [{"type": "unknown", "command": "cargo build"}]})
                note(m="item/started", threadId=tid, turnId=turn, startedAtMs=0, item={
                    "type": "fileChange", "id": "f1", "status": "inProgress",
                    "changes": [{"path": ws + "/web/app.js", "kind": {"type": "update", "move_path": None}, "diff": ""}]})
                note(m="item/started", threadId=tid, turnId=turn, startedAtMs=0, item={
                    "type": "webSearch", "id": "w1", "query": "vitest config", "action": None})
                note(m="item/started", threadId=tid, turnId=turn, startedAtMs=0, item={
                    "type": "mcpToolCall", "id": "t1", "server": "github", "tool": "get issue", "status": "inProgress",
                    "arguments": {}})
                note(m="turn/diff/updated", threadId=tid, turnId=turn, diff=os.environ["FAKE_DIFF"])
                note(m="item/agentMessage/delta", threadId=tid, turnId=turn, itemId="m2", delta="All ")
                agent(tid, turn, "m2", "All done.", "final_answer")
                done(tid, turn, "completed")
            elif prompt == "legacy phase":
                agent(tid, turn, "m1", "Looking around first.", None)
                agent(tid, turn, "m2", "Here is the answer.", None)
                done(tid, turn, "completed")
            elif prompt in ("approve command", "approve file", "approve perms", "approve legacy"):
                if prompt == "approve file":
                    note(m="item/started", threadId=tid, turnId=turn, startedAtMs=0, item={
                        "type": "fileChange", "id": "f9", "status": "inProgress",
                        "changes": [{"path": ws + "/src/main.py", "kind": {"type": "add"}, "diff": ""}]})
                    send({"id": "srv_%d" % turns, "method": "item/fileChange/requestApproval", "params": {
                        "threadId": tid, "turnId": turn, "itemId": "f9", "startedAtMs": 0}})
                elif prompt == "approve perms":
                    send({"id": "srv_%d" % turns, "method": "item/permissions/requestApproval", "params": {
                        "threadId": tid, "turnId": turn, "itemId": "p1", "startedAtMs": 0, "environmentId": None,
                        "cwd": ws, "reason": None, "permissions": {"network": {"enabled": True}, "fileSystem": None}}})
                elif prompt == "approve legacy":
                    send({"id": "srv_%d" % turns, "method": "execCommandApproval", "params": {
                        "conversationId": tid, "callId": "x", "approvalId": None, "command": ["rm", "-rf", "build"],
                        "cwd": ws, "reason": None, "parsedCmd": []}})
                else:
                    send({"id": "srv_%d" % turns, "method": "item/commandExecution/requestApproval", "params": {
                        "threadId": tid, "turnId": turn, "itemId": "c1", "startedAtMs": 0, "environmentId": None,
                        "command": "npm test", "cwd": ws + "/web"}})
                waiting["srv_%d" % turns] = (tid, turn)
            elif prompt == "crash":
                sys.exit(3)
            elif prompt == "fail":
                done(tid, turn, "failed", {"message": json.dumps({"type": "error", "status": 429, "error": {
                    "type": "usage", "message": "usage limit reached"}}), "codexErrorInfo": None,
                                           "additionalDetails": None})
            # "wait": stay in progress until steered / interrupted
        elif method == "turn/steer":
            send({"id": mid, "result": {"turnId": p["expectedTurnId"]}})
        elif method == "turn/interrupt":
            send({"id": mid, "result": {}})
            done(p["threadId"], p["turnId"], "interrupted")
        elif method == "thread/read":
            text = lambda t: [{"type": "text", "text": t, "text_elements": []}]
            send({"id": mid, "result": {"thread": {"id": p["threadId"], "turns": [
                {"id": "t1", "status": "completed", "items": [
                    {"type": "userMessage", "id": "u1", "content": text("first question")},
                    {"type": "reasoning", "id": "r1", "summary": [], "content": []},
                    {"type": "agentMessage", "id": "a0", "text": "let me look", "phase": "commentary"},
                    {"type": "agentMessage", "id": "a1", "text": "first answer", "phase": "final_answer"}]},
                {"id": "t2", "status": "completed", "items": [
                    {"type": "userMessage", "id": "u2", "content": text("second question")},
                    {"type": "agentMessage", "id": "a2", "text": "second answer", "phase": None}]}]}}})
        else:
            send({"id": mid, "error": {"code": -32601, "message": "unknown method " + method}})
''')


class Harness:
    def __init__(self, tmp_path: Path):
        self.script = tmp_path / "fake_app_server.py"
        self.script.write_text(FAKE_APP_SERVER)
        self.log = tmp_path / "fake-log.jsonl"
        self.ws = tmp_path / "ws"
        self.ws.mkdir()
        (self.ws / "web").mkdir()
        self.spawns: list[list[str]] = []

    def spawn(self, argv, cwd, env):
        self.spawns.append(argv)
        assert cwd == str(self.ws.resolve())
        assert "OPENAI_API_KEY" not in env and "API_SERVER_KEY" not in env
        env = {**env, "FAKE_LOG": str(self.log), "FAKE_WS": str(self.ws.resolve()), "FAKE_DIFF": DIFF}
        return subprocess.Popen([sys.executable, str(self.script), *argv[1:]], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                cwd=cwd, env=env)

    def sent(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def methods(self) -> list[str]:
        return [str(m["method"]) for m in self.sent() if m.get("method")]


@pytest.fixture
def h(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("API_SERVER_KEY", "must-not-leak")
    return Harness(tmp_path)


@pytest.fixture
def backend(h):
    b = CodexBackend(h.ws, spawn=h.spawn)
    yield b
    b.close()


def collect(backend, run_id) -> tuple[bool, list[dict]]:
    seen: list[dict] = []
    return backend.events(run_id, seen.append), seen


def events_in_background(backend, run_id):
    seen: list[dict] = []
    result: dict = {}

    def run():
        result["terminal"] = backend.events(run_id, seen.append)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, seen, result


def test_is_a_task_backend_with_codex_capabilities(backend):
    assert isinstance(backend, TaskBackend)
    caps = backend.capabilities()
    assert (caps.kind, caps.display_name) == ("codex", "Codex")
    assert caps.steer and caps.approvals and caps.file_changes and caps.needs_workspace
    assert not (caps.chat_delivery or caps.threads or caps.conversation_continuity or caps.email_drafts
                or caps.daily_brief)


def test_workspace_must_exist(tmp_path):
    with pytest.raises(BackendError) as err:
        CodexBackend(tmp_path / "missing")
    assert err.value.status == 400
    (tmp_path / "file").write_text("x")
    with pytest.raises(BackendError):
        CodexBackend(tmp_path / "file")


def test_start_events_completed_with_output_tools_and_files(backend, h):
    run_id = backend.start_run("hello", "idem-1")
    assert ID_RE.fullmatch(run_id)
    terminal, seen = collect(backend, run_id)
    assert terminal
    kinds = [e["event"] for e in seen]
    assert kinds[0] == "run.started" and kinds[-1] == "run.completed"
    assert seen[-1]["output"] == "All done."
    interim = [e for e in seen if e["event"] == "message.interim"]
    assert len(interim) == 1 and interim_progress(interim[0]) == ("Checking the tests", "Looking at the test suite.")
    tools = [(e["tool"], e["preview"]) for e in seen if e["event"] == "tool.started"]
    assert ("terminal", "") in tools            # `npm test --token=abc` looks secret: preview dropped
    assert ("terminal", "cargo build") in tools
    assert ("edit_file", "web/app.js") in tools  # workspace-relative, no home path
    assert ("web_search", "vitest config") in tools
    assert ("github.get_issue", "") in tools
    assert not any(str(h.ws.resolve()) in json.dumps(e) for e in seen)
    changed = [e for e in seen if e["event"] == "files.changed"]
    assert changed[0]["files"] == [{"path": "web/app.js", "added": 2, "removed": 1},
                                   {"path": "notes.txt", "added": 1, "removed": 0}]
    assert changed[0]["diff"] == DIFF
    assert h.methods()[:4] == ["initialize", "initialized", "thread/start", "turn/start"]
    init = h.sent()[0]["params"]
    assert init["clientInfo"]["name"] == "speakeasy"
    start = next(m for m in h.sent() if m.get("method") == "thread/start")["params"]
    assert start == {"cwd": str(h.ws.resolve()), "sandbox": "workspace-write", "approvalPolicy": "on-request"}
    turn = next(m for m in h.sent() if m.get("method") == "turn/start")["params"]
    assert turn["threadId"] == "thr_new" and turn["input"] == [{"type": "text", "text": "hello", "text_elements": []}]
    assert backend.get_run(run_id) == {"run_id": run_id, "status": "completed", "output": "All done.",
                                       "session_id": "thr_new"}
    # Replay: a second reader gets the same log from the start.
    again_terminal, again = collect(backend, run_id)
    assert again_terminal and [e["event"] for e in again] == kinds


def test_unknown_phase_uses_last_agent_message(backend):
    status, output = backend.run_to_completion("legacy phase", "idem-legacy")
    assert (status, output) == ("completed", "Here is the answer.")


def test_idempotent_start_never_starts_a_second_turn(backend, h):
    first = backend.start_run("hello", "same-key")
    assert backend.start_run("hello", "same-key") == first
    collect(backend, first)
    assert h.methods().count("turn/start") == 1 and len(h.spawns) == 1


def test_resume_via_session_id_uses_thread_resume(backend, h):
    run_id = backend.start_run("hello", "idem-r", session_id="thr_old")
    collect(backend, run_id)
    resume = next(m for m in h.sent() if m.get("method") == "thread/resume")["params"]
    assert resume["threadId"] == "thr_old" and resume["cwd"] == str(h.ws.resolve())
    assert "thread/start" not in h.methods()
    assert backend.get_run(run_id)["session_id"] == "thr_old"
    # A follow-up on a thread already loaded in this child goes straight to turn/start.
    follow = backend.start_run("hello", "idem-r2", session_id="thr_old")
    collect(backend, follow)
    assert h.methods().count("thread/resume") == 1 and h.methods().count("turn/start") == 2


@pytest.mark.parametrize("prompt,kind,description,once,deny", [
    ("approve command", "command", "Run `npm test` in web/.", "accept", "decline"),
    ("approve file", "file_change", "Edit src/main.py.", "accept", "decline"),
    ("approve legacy", "command", "Run `rm -rf build` in the project.", "approved", "denied"),
])
@pytest.mark.parametrize("choice", ["once", "deny"])
def test_approval_round_trip(backend, h, prompt, kind, description, once, deny, choice):
    run_id = backend.start_run(prompt, f"idem-{prompt}-{choice}")
    t, seen, result = events_in_background(backend, run_id)
    request = wait_for(lambda: next((e for e in seen if e["event"] == "approval.request"), None))
    assert ID_RE.fullmatch(request["request_id"]) and request["kind"] == kind
    assert request["description"] == description
    assert backend.get_run(run_id)["status"] == "waiting_for_approval"
    backend.approve(run_id, request["request_id"], choice)
    t.join(10)
    assert result["terminal"]
    expected = once if choice == "once" else deny
    reply = next(m for m in h.sent() if str(m.get("id", "")).startswith("srv_") and "method" not in m)
    assert reply["result"] == {"decision": expected} and "method" not in reply
    assert seen[-1] == {"event": "run.completed", "run_id": run_id, "output": f"decision={expected}"}
    with pytest.raises(BackendError):  # already answered
        backend.approve(run_id, request["request_id"], "once")


def test_permissions_approval_grants_requested_or_nothing(backend, h):
    for choice, granted in (("once", {"network": {"enabled": True}}), ("deny", {})):
        run_id = backend.start_run("approve perms", f"idem-perm-{choice}")
        t, seen, _ = events_in_background(backend, run_id)
        request = wait_for(lambda: next((e for e in seen if e["event"] == "approval.request"), None))
        assert request["kind"] == "permission" and request["description"] == "Allow network access for this task."
        backend.approve(run_id, request["request_id"], choice)
        t.join(10)
        reply = [m for m in h.sent() if str(m.get("id", "")).startswith("srv_") and "method" not in m][-1]
        assert reply["result"] == {"permissions": granted, "scope": "turn"}


def test_steer_and_stop_map_to_turn_steer_and_interrupt(backend, h):
    run_id = backend.start_run("wait", "idem-wait")
    t, seen, result = events_in_background(backend, run_id)
    wait_for(lambda: any(e["event"] == "run.started" for e in seen))
    assert backend.steer(run_id, "use pnpm instead") is True
    steer = next(m for m in h.sent() if m.get("method") == "turn/steer")["params"]
    assert steer == {"threadId": "thr_new", "expectedTurnId": "turn_1",
                     "input": [{"type": "text", "text": "use pnpm instead", "text_elements": []}]}
    backend.stop(run_id)
    t.join(10)
    assert result["terminal"] and seen[-1]["event"] == "run.cancelled"
    interrupt = next(m for m in h.sent() if m.get("method") == "turn/interrupt")["params"]
    assert interrupt == {"threadId": "thr_new", "turnId": "turn_1"}
    assert backend.get_run(run_id)["status"] == "cancelled"
    assert backend.steer(run_id, "too late") is False


def test_failed_turn_is_run_failed(backend):
    status, output = backend.run_to_completion("fail", "idem-fail")
    assert (status, output) == ("failed", "")
    assert backend.last_error == "Codex error: usage limit reached"


def test_child_crash_yields_run_failed_and_restarts(backend, h):
    run_id = backend.start_run("crash", "idem-crash")
    terminal, seen = collect(backend, run_id)
    assert terminal and seen[-1]["event"] == "run.failed" and "exited" in seen[-1]["error"]
    # The next task starts a fresh child.
    status, output = backend.run_to_completion("hello", "idem-after-crash")
    assert (status, output) == ("completed", "All done.") and len(h.spawns) == 2


def test_session_messages_from_thread_read(backend, h):
    messages = backend.session_messages("thr_old")
    assert messages == [{"role": "user", "content": "first question"},
                        {"role": "assistant", "content": "first answer"},
                        {"role": "user", "content": "second question"},
                        {"role": "assistant", "content": "second answer"}]
    assert backend.session_messages("thr_old", limit=1) == [{"role": "assistant", "content": "second answer"}]
    read = next(m for m in h.sent() if m.get("method") == "thread/read")["params"]
    assert read == {"threadId": "thr_old", "includeTurns": True}
    assert backend.session_messages("bad id!") == []


def test_unknown_run_and_bad_ids(backend):
    with pytest.raises(BackendError) as err:
        backend.get_run("cx_" + "0" * 32)
    assert err.value.status == 404
    with pytest.raises(BackendError) as err:
        backend.events("../etc", lambda e: None)
    assert err.value.status == 400


def test_missing_binary_is_backend_unavailable(h):
    b = CodexBackend(h.ws, codex_bin=str(h.ws / "no-such-codex"))
    assert b.health() is False
    with pytest.raises(BackendError) as err:
        b.start_run("hello", "idem-x")
    assert err.value.status == 502
    # A failed start does not burn the idempotency key.
    with pytest.raises(BackendError):
        b.start_run("hello", "idem-x")


def test_parse_diff_stats_handles_deletes():
    diff = "diff --git a/gone.txt b/gone.txt\ndeleted file mode 100644\n--- a/gone.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
    assert parse_diff_stats(diff) == [{"path": "gone.txt", "added": 0, "removed": 2}]


def test_diff_is_capped(backend):
    big = "diff --git a/big.txt b/big.txt\n--- a/big.txt\n+++ b/big.txt\n" + "+x\n" * 40000
    with backend._lock:  # drive the translation directly: the fake's diff is small
        from speakeasy.backends.codex import _Run
        run = _Run(run_id="cx_diff", idem_key="k", thread_id="thr_d", turn_id="turn_d", status="running")
        backend._runs[run.run_id] = run
        backend._by_thread["thr_d"] = run.run_id
    backend._notification("turn/diff/updated", {"threadId": "thr_d", "turnId": "turn_d", "diff": big})
    event = [e for e in run.events if e["event"] == "files.changed"][-1]
    assert len(event["diff"].encode()) <= 64 * 1024
    assert event["files"] == [{"path": "big.txt", "added": 40000, "removed": 0}]
