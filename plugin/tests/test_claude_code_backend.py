"""ClaudeCodeBackend against a fake ``claude`` CLI speaking stream-json (tests/fake_claude.py)."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from speakeasy.backends import BackendError, Capabilities, TaskBackend
from speakeasy.backends.claude_code import ClaudeCodeBackend, tool_short_name
from speakeasy.text import ID_RE, TERMINAL

FAKE = Path(__file__).with_name("fake_claude.py")


@pytest.fixture
def fake_bin(tmp_path):
    exe = tmp_path / "bin" / "claude"
    exe.parent.mkdir()
    exe.write_text(f"#!{sys.executable}\n" + FAKE.read_text())
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("hello\n")
    return ws


def make(fake_bin, workspace, tmp_path, scenario, **kw):
    log = tmp_path / f"log-{scenario}-{uuid.uuid4().hex[:6]}.jsonl"
    env = dict(os.environ, FAKE_CC_SCENARIO=scenario, FAKE_CC_LOG=str(log))
    backend = ClaudeCodeBackend(workspace, claude_bin=fake_bin, env=env, stop_grace_s=3, exit_grace_s=3, **kw)
    return backend, log


def read_log(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def stdin_msgs(log):
    return [e["value"] for e in read_log(log) if e["kind"] == "stdin"]


def argv(log):
    return next(e["value"] for e in read_log(log) if e["kind"] == "argv")


def collect(backend, run_id, on_event=None, timeout=20):
    events: list[dict] = []
    done = threading.Event()
    result = {}

    def run():
        def cb(event):
            events.append(event)
            if on_event:
                on_event(event)
        result["terminal"] = backend.events(run_id, cb)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(timeout), f"events() did not finish; got {[e['event'] for e in events]}"
    return events, result["terminal"]


def kinds(events):
    return [e["event"] for e in events]


def test_is_a_task_backend_with_capabilities(fake_bin, workspace):
    backend = ClaudeCodeBackend(workspace, claude_bin=fake_bin)
    assert isinstance(backend, TaskBackend)
    caps = backend.capabilities()
    assert isinstance(caps, Capabilities)
    assert (caps.kind, caps.display_name) == ("claude_code", "Claude Code")
    assert caps.steer and caps.approvals and caps.file_changes and caps.needs_workspace
    assert not (caps.chat_delivery or caps.threads or caps.conversation_continuity or caps.email_drafts
                or caps.daily_brief)
    assert backend.health()


def test_completed_with_output_and_tool_mapping(fake_bin, workspace, tmp_path):
    backend, log = make(fake_bin, workspace, tmp_path, "complete")
    run_id = backend.start_run("What does the readme say?", "idem-1")
    assert ID_RE.fullmatch(run_id)
    events, terminal = collect(backend, run_id)
    assert terminal
    assert kinds(events) == ["run.started", "message.interim", "tool.started", "tool.started", "tool.started",
                             "run.completed"]
    assert events[1]["text"].startswith("STATUS: Checking the readme")
    tools = [(e["tool"], e["preview"]) for e in events if e["event"] == "tool.started"]
    assert tools == [("read_file", "README.md"), ("search_files", "TODO"), ("mcp__x__thing", "")]
    assert events[-1]["output"] == "The readme says hello."
    run = backend.get_run(run_id)
    assert run["status"] == "completed" and run["output"] == "The readme says hello."
    assert uuid.UUID(run["session_id"])
    # Prompt goes through stdin as a stream-json user message; flags are the verified ones.
    a = argv(log)
    assert a[:8] == ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                     "--permission-prompt-tool", "stdio"]
    assert a[a.index("--session-id") + 1] == run["session_id"]
    assert "--resume" not in a and "--permission-mode" not in a
    first = stdin_msgs(log)[0]
    assert first["type"] == "user" and first["message"] == {"role": "user", "content": "What does the readme say?"}
    # Replaying after the end gives the same events.
    again, terminal2 = collect(backend, run_id)
    assert terminal2 and kinds(again) == kinds(events)
    assert backend.run_to_completion("again", "idem-rtc") == ("completed", "The readme says hello.")


def test_idempotent_start_never_spawns_twice(fake_bin, workspace, tmp_path):
    backend, log = make(fake_bin, workspace, tmp_path, "complete")
    first = backend.start_run("hi", "same-key")
    second = backend.start_run("hi", "same-key")
    assert first == second
    collect(backend, first)
    time.sleep(0.2)
    assert sum(1 for e in read_log(log) if e["kind"] == "argv") == 1
    assert backend.start_run("hi", "other-key") != first


def test_approval_once_allows_with_original_input(fake_bin, workspace, tmp_path):
    backend, log = make(fake_bin, workspace, tmp_path, "approval")
    run_id = backend.start_run("run the tests", "idem-ap")

    def on_event(event):
        if event["event"] == "approval.request":
            assert backend.get_run(run_id)["status"] == "waiting_for_approval"
            backend.approve(run_id, event["request_id"], "once")

    events, _ = collect(backend, run_id, on_event)
    req = next(e for e in events if e["event"] == "approval.request")
    assert req["kind"] == "command" and req["description"] == "Run `npm test` in ws"
    assert ID_RE.fullmatch(req["request_id"]) and req["request_id"] != "cli_req_1"
    assert events[-1]["event"] == "run.completed" and events[-1]["output"] == "Tests pass."
    response = next(m for m in stdin_msgs(log) if m["type"] == "control_response")
    assert response["response"]["subtype"] == "success"
    assert response["response"]["request_id"] == "cli_req_1"
    decision = response["response"]["response"]
    assert decision["behavior"] == "allow"
    assert decision["updatedInput"] == {"command": "npm test", "description": "Run tests"}
    with pytest.raises(BackendError) as exc:
        backend.approve(run_id, req["request_id"], "once")
    assert exc.value.status == 409


def test_approval_deny(fake_bin, workspace, tmp_path):
    backend, log = make(fake_bin, workspace, tmp_path, "approval")
    run_id = backend.start_run("run the tests", "idem-deny")
    with pytest.raises(BackendError):
        backend.approve(run_id, "ap_x", "always")

    def on_event(event):
        if event["event"] == "approval.request":
            backend.approve(run_id, event["request_id"], "deny")

    events, _ = collect(backend, run_id, on_event)
    assert events[-1]["output"] == "I did not run the tests."
    decision = next(m for m in stdin_msgs(log) if m["type"] == "control_response")["response"]["response"]
    assert decision["behavior"] == "deny" and decision["message"]
    assert "updatedInput" not in decision


def test_files_changed_after_edit_in_git_repo(fake_bin, workspace, tmp_path):
    git = ["git", "-C", str(workspace), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(git + ["init", "-q"], check=True)
    (workspace / "hello.txt").write_text("hello\n")
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-qm", "init"], check=True)
    backend, _ = make(fake_bin, workspace, tmp_path, "edit")
    run_id = backend.start_run("update greeting", "idem-edit")

    def on_event(event):
        if event["event"] == "approval.request":
            backend.approve(run_id, event["request_id"], "once")

    events, _ = collect(backend, run_id, on_event)
    assert kinds(events) == ["run.started", "message.interim", "tool.started", "approval.request",
                             "files.changed", "run.completed"]
    req = events[3]
    assert req["kind"] == "file_change" and req["description"] == "Edit hello.txt"
    changed = events[4]
    files = {f["path"]: (f["added"], f["removed"]) for f in changed["files"]}
    assert files == {"hello.txt": (2, 1), "new.txt": (1, 0)}
    assert "+hello world" in changed["diff"] and "+brand new" in changed["diff"]
    # The index was left alone.
    status = subprocess.run(git + ["status", "--porcelain"], capture_output=True, text=True).stdout
    assert "?? new.txt" in status


def test_no_files_changed_outside_git(fake_bin, workspace, tmp_path):
    (workspace / "hello.txt").write_text("hello\n")
    backend, _ = make(fake_bin, workspace, tmp_path, "edit")
    run_id = backend.start_run("update greeting", "idem-edit-nogit")
    events, _ = collect(backend, run_id, lambda e: e["event"] == "approval.request"
                        and backend.approve(run_id, e["request_id"], "once"))
    assert "files.changed" not in kinds(events) and events[-1]["event"] == "run.completed"


def test_steer_writes_user_message_and_run_ends_after_steered_turn(fake_bin, workspace, tmp_path):
    backend, log = make(fake_bin, workspace, tmp_path, "steer")
    run_id = backend.start_run("list files", "idem-steer")
    steered = {}

    def on_event(event):
        if event["event"] == "tool.started" and not steered:
            steered["ok"] = backend.steer(run_id, "only show python files")

    events, _ = collect(backend, run_id, on_event)
    assert steered["ok"] is True
    msgs = [m for m in stdin_msgs(log) if m["type"] == "user"]
    assert msgs[1]["message"] == {"role": "user", "content": "only show python files"}
    assert msgs[1]["session_id"] == backend.get_run(run_id)["session_id"]
    assert events[-1]["event"] == "run.completed"
    assert events[-1]["output"] == "Guided: only show python files"
    assert backend.steer(run_id, "too late") is False
    assert backend.steer("cc_unknown", "x") is False


def test_stop_sends_interrupt_and_cancels(fake_bin, workspace, tmp_path):
    backend, log = make(fake_bin, workspace, tmp_path, "hang")
    run_id = backend.start_run("sleep", "idem-stop")
    got = {}

    def on_event(event):
        if event["event"] == "tool.started":
            got["stop"] = backend.stop(run_id)

    events, terminal = collect(backend, run_id, on_event)
    assert terminal and events[-1]["event"] == "run.cancelled"
    assert got["stop"]["status"] == "cancelled" and "cancelled" in TERMINAL
    interrupt = [m for m in stdin_msgs(log) if m["type"] == "control_request"]
    assert interrupt and interrupt[0]["request"] == {"subtype": "interrupt"}
    assert backend.steer(run_id, "x") is False


def test_stop_kills_a_process_that_ignores_interrupt(fake_bin, workspace, tmp_path):
    backend, _ = make(fake_bin, workspace, tmp_path, "hang_ignore")
    backend.stop_grace_s = 0.5
    run_id = backend.start_run("sleep", "idem-kill")
    events, _ = collect(backend, run_id, lambda e: e["event"] == "tool.started" and backend.stop(run_id))
    assert events[-1]["event"] == "run.cancelled"
    assert backend.get_run(run_id)["status"] == "cancelled"


def test_resume_passes_resume_uuid(fake_bin, workspace, tmp_path):
    backend, log = make(fake_bin, workspace, tmp_path, "resume")
    sid = str(uuid.uuid4())
    run_id = backend.start_run("continue", "idem-resume", session_id=sid)
    collect(backend, run_id)
    a = argv(log)
    assert a[a.index("--resume") + 1] == sid and "--session-id" not in a
    assert backend.get_run(run_id)["session_id"] == sid
    with pytest.raises(BackendError) as exc:
        backend.start_run("x", "idem-bad", session_id="not-a-uuid")
    assert exc.value.status == 400


def test_model_and_permission_mode_flags(fake_bin, workspace, tmp_path):
    backend, log = make(fake_bin, workspace, tmp_path, "resume", model="sonnet", permission_mode="acceptEdits")
    collect(backend, backend.start_run("x", "idem-flags"))
    a = argv(log)
    assert a[a.index("--model") + 1] == "sonnet"
    assert a[a.index("--permission-mode") + 1] == "acceptEdits"
    cwd = next(e["value"] for e in read_log(log) if e["kind"] == "cwd")
    assert os.path.realpath(cwd) == os.path.realpath(workspace)


def test_auth_error_result_fails_with_readable_error(fake_bin, workspace, tmp_path):
    backend, _ = make(fake_bin, workspace, tmp_path, "auth")
    run_id = backend.start_run("hi", "idem-auth")
    events, terminal = collect(backend, run_id)
    assert terminal and events[-1]["event"] == "run.failed"
    assert "message.interim" not in kinds(events)  # the synthetic error text is not commentary
    assert events[-1]["error"] == "Failed to authenticate: OAuth session expired and could not be refreshed"
    assert backend.get_run(run_id)["status"] == "failed"
    status, output = backend.run_to_completion("hi", "idem-auth-2")
    assert status == "failed" and output == "" and "OAuth session expired" in backend.last_error


def test_crash_without_result_fails_not_hangs(fake_bin, workspace, tmp_path):
    backend, _ = make(fake_bin, workspace, tmp_path, "crash")
    run_id = backend.start_run("hi", "idem-crash")
    events, terminal = collect(backend, run_id)
    assert terminal and events[-1]["event"] == "run.failed"
    assert "without a result" in events[-1]["error"] and "something broke" in events[-1]["error"]


def test_errors(fake_bin, workspace, tmp_path):
    with pytest.raises(BackendError) as exc:
        ClaudeCodeBackend(tmp_path / "missing", claude_bin=fake_bin).start_run("x", "k")
    assert exc.value.status == 400
    with pytest.raises(BackendError) as exc:
        ClaudeCodeBackend(workspace, claude_bin=tmp_path / "nope").start_run("x", "k")
    assert exc.value.status == 502
    backend = ClaudeCodeBackend(workspace, claude_bin=fake_bin)
    for bad in ("bad id!", "cc_missing"):
        with pytest.raises(BackendError):
            backend.get_run(bad)
    assert not ClaudeCodeBackend(workspace, claude_bin=tmp_path / "nope").health()


def test_session_messages_reads_cli_transcript(workspace, tmp_path):
    projects = tmp_path / "projects"
    sid = str(uuid.uuid4())
    folder = projects / str(workspace).replace("/", "-").replace("_", "-").replace(".", "-")
    folder.mkdir(parents=True)
    lines = [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": sid},
        {"type": "user", "isSidechain": False, "message": {"role": "user", "content": "fix the bug"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Looking."}, {"type": "tool_use", "id": "t", "name": "Read", "input": {}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t",
                                                                  "content": "..."}]}},
        {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": "subagent"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Fixed."}]}},
    ]
    (folder / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    backend = ClaudeCodeBackend(workspace, claude_bin="claude", projects_dir=projects)
    assert backend.session_messages(sid) == [
        {"role": "user", "content": "fix the bug"}, {"role": "assistant", "content": "Looking."},
        {"role": "assistant", "content": "Fixed."}]
    assert backend.session_messages(sid, limit=1) == [{"role": "assistant", "content": "Fixed."}]
    assert backend.session_messages(str(uuid.uuid4())) == []
    assert backend.session_messages("../etc/passwd") == []


def test_media_seen_for_viewed_image_file_and_inline_screenshot(fake_bin, workspace, tmp_path):
    (workspace / "design.png").write_bytes(b"\x89PNG fake")
    media = tmp_path / "speakeasy-media"
    backend, _ = make(fake_bin, workspace, tmp_path, "media", media_root=media)
    run_id = backend.start_run("look at the design", "idem-media")
    events, _ = collect(backend, run_id)
    seen = [e for e in events if e["event"] == "media.seen"]
    assert len(seen) == 2  # the Read's inline copy is not shown twice; the bad base64 is skipped
    viewed, shot = seen
    assert viewed["source"] == "viewed" and viewed["name"] == "design.png"
    assert os.path.isabs(viewed["path"]) and os.path.samefile(viewed["path"], workspace / "design.png")
    assert shot["source"] == "screenshot" and os.path.isabs(shot["path"])
    assert Path(shot["path"]).parent == (media / run_id).resolve() and Path(shot["path"]).name == "1.png"
    assert Path(shot["path"]).read_bytes().startswith(b"\x89PNG")
    assert events[-1]["event"] == "run.completed"


def test_media_seen_inline_image_when_read_file_is_gone(fake_bin, workspace, tmp_path):
    media = tmp_path / "speakeasy-media"
    backend, _ = make(fake_bin, workspace, tmp_path, "media", media_root=media)
    run_id = backend.start_run("look", "idem-media-2")
    events, _ = collect(backend, run_id)
    seen = [e for e in events if e["event"] == "media.seen"]
    assert [e["source"] for e in seen] == ["viewed", "screenshot"]
    assert [Path(e["path"]).name for e in seen] == ["1.png", "2.png"]
    assert all(Path(e["path"]).is_file() for e in seen)


def test_tool_short_names():
    assert [tool_short_name(n) for n in ("Bash", "Read", "Grep", "Glob", "Edit", "Write", "MultiEdit",
                                         "WebFetch", "WebSearch", "TodoWrite", None)] == [
        "terminal", "read_file", "search_files", "search_files", "edit_file", "edit_file", "edit_file",
        "web_search", "web_search", "todowrite", "tool"]
