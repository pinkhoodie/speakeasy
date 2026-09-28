#!/usr/bin/env python3
"""A fake ``claude`` CLI for ClaudeCodeBackend tests.

Speaks the stream-json protocol of ``claude -p --input-format stream-json --output-format
stream-json --verbose --permission-prompt-tool stdio`` (shapes captured from Claude Code 2.1.207).
The scenario comes from ``FAKE_CC_SCENARIO``; argv and every stdin line are appended to
``FAKE_CC_LOG`` (JSONL) so tests can assert what the backend sent.
"""
import json
import os
import sys
import uuid

LOG = os.environ.get("FAKE_CC_LOG")
SCENARIO = os.environ.get("FAKE_CC_SCENARIO", "complete")


def log(kind, value):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"kind": kind, "value": value}) + "\n")


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        return None
    msg = json.loads(line)
    log("stdin", msg)
    return msg


def arg(name):
    argv = sys.argv[1:]
    return argv[argv.index(name) + 1] if name in argv else None


SID = arg("--session-id") or arg("--resume") or str(uuid.uuid4())


def init():
    out({"type": "system", "subtype": "init", "cwd": os.getcwd(), "session_id": SID,
         "tools": ["Bash", "Edit", "Read", "Write"], "mcp_servers": [], "model": "claude-fake",
         "permissionMode": "default", "apiKeySource": "none", "uuid": str(uuid.uuid4())})


def assistant(*blocks):
    out({"type": "assistant", "message": {"id": "msg_" + uuid.uuid4().hex[:8], "type": "message", "role": "assistant",
         "model": "claude-fake", "content": list(blocks), "stop_reason": None},
         "parent_tool_use_id": None, "session_id": SID, "uuid": str(uuid.uuid4())})


def tool_result(tool_use_id, content, is_error=False):
    out({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}]},
        "parent_tool_use_id": None, "session_id": SID, "uuid": str(uuid.uuid4())})


def result(text, is_error=False, subtype="success"):
    out({"type": "result", "subtype": subtype, "is_error": is_error, "duration_ms": 5, "num_turns": 1,
         "result": text, "session_id": SID, "total_cost_usd": 0, "permission_denials": [],
         "uuid": str(uuid.uuid4())})


def ask(request_id, tool, tool_input, tool_use_id):
    out({"type": "control_request", "request_id": request_id, "request": {
        "subtype": "can_use_tool", "tool_name": tool, "display_name": tool, "input": tool_input,
        "tool_use_id": tool_use_id, "permission_suggestions": []}})
    while True:
        msg = read()
        if msg is None:
            sys.exit(0)
        if msg.get("type") == "control_response" and msg["response"].get("request_id") == request_id:
            return msg["response"]["response"]


def drain():
    """Real CLI keeps reading stdin after a result; exit on EOF like it does."""
    while read() is not None:
        pass


def main():
    log("argv", sys.argv[1:])
    log("cwd", os.getcwd())
    first = read()
    if first is None:
        return
    if SCENARIO == "auth":
        # Exactly what the logged-out CLI printed in a live probe.
        init()
        out({"type": "assistant", "message": {"id": str(uuid.uuid4()), "model": "<synthetic>", "role": "assistant",
             "stop_reason": "stop_sequence", "type": "message", "content": [
                 {"type": "text", "text": "Failed to authenticate: OAuth session expired and could not be refreshed"}]},
             "parent_tool_use_id": None, "session_id": SID, "uuid": str(uuid.uuid4()), "error": "authentication_failed"})
        out({"type": "result", "subtype": "success", "is_error": True, "api_error_status": None, "duration_ms": 28,
             "num_turns": 1, "result": "Failed to authenticate: OAuth session expired and could not be refreshed",
             "stop_reason": "stop_sequence", "session_id": SID, "total_cost_usd": 0, "permission_denials": [],
             "terminal_reason": "api_error", "uuid": str(uuid.uuid4())})
        sys.exit(1)
    init()
    if SCENARIO == "crash":
        sys.stderr.write("fatal: something broke\n")
        sys.exit(3)
    if SCENARIO == "complete":
        assistant({"type": "text", "text": "STATUS: Checking the readme\nDETAIL: Reading README.md"})
        assistant({"type": "tool_use", "id": "toolu_1", "name": "Read",
                   "input": {"file_path": os.path.join(os.getcwd(), "README.md")}})
        tool_result("toolu_1", "hello")
        assistant({"type": "tool_use", "id": "toolu_2", "name": "Grep", "input": {"pattern": "TODO"}})
        tool_result("toolu_2", "none")
        assistant({"type": "tool_use", "id": "toolu_3", "name": "mcp__x__Thing", "input": {}})
        tool_result("toolu_3", "ok")
        assistant({"type": "text", "text": "The readme says hello."})
        result("The readme says hello.")
        drain()
    elif SCENARIO == "approval":
        assistant({"type": "tool_use", "id": "toolu_b", "name": "Bash",
                   "input": {"command": "npm test", "description": "Run tests"}})
        decision = ask("cli_req_1", "Bash", {"command": "npm test", "description": "Run tests"}, "toolu_b")
        if decision["behavior"] == "allow":
            tool_result("toolu_b", "12 passing")
            result("Tests pass.")
        else:
            tool_result("toolu_b", decision.get("message", ""), is_error=True)
            result("I did not run the tests.")
        drain()
    elif SCENARIO == "edit":
        path = os.path.join(os.getcwd(), "hello.txt")
        inp = {"file_path": path, "old_string": "hello", "new_string": "hello world"}
        assistant({"type": "text", "text": "I'll update the greeting."})
        assistant({"type": "tool_use", "id": "toolu_e", "name": "Edit", "input": inp})
        decision = ask("cli_req_e", "Edit", inp, "toolu_e")
        if decision["behavior"] == "allow":
            with open(path, "w") as fh:
                fh.write("hello world\nsecond line\n")
            with open(os.path.join(os.getcwd(), "new.txt"), "w") as fh:
                fh.write("brand new\n")
            tool_result("toolu_e", "edited")
        result("Updated hello.txt.")
        drain()
    elif SCENARIO == "steer":
        assistant({"type": "tool_use", "id": "toolu_s", "name": "Bash", "input": {"command": "ls"}})
        steer = read()
        text = steer["message"]["content"] if steer else ""
        tool_result("toolu_s", "a b c")
        result(f"First turn done")
        # The queued steer runs as the next turn.
        assistant({"type": "text", "text": f"Following guidance: {text}"})
        result(f"Guided: {text}")
        drain()
    elif SCENARIO in {"hang", "hang_ignore"}:
        assistant({"type": "tool_use", "id": "toolu_h", "name": "Bash", "input": {"command": "sleep 100"}})
        while True:
            msg = read()
            if msg is None:
                if SCENARIO == "hang_ignore":
                    import time
                    time.sleep(60)
                return
            if msg.get("type") == "control_request" and msg["request"]["subtype"] == "interrupt":
                out({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"],
                     "response": {"still_queued": []}}})
                if SCENARIO == "hang":
                    result("", is_error=True, subtype="error_during_execution")
    elif SCENARIO == "media":
        png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        # Read of an image file in the workspace: the CLI returns it inline as well.
        assistant({"type": "tool_use", "id": "toolu_r", "name": "Read",
                   "input": {"file_path": os.path.join(os.getcwd(), "design.png")}})
        out({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_r",
             "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png}}]}]},
             "parent_tool_use_id": None, "session_id": SID})
        # A browser MCP screenshot: inline image only.
        assistant({"type": "tool_use", "id": "toolu_p", "name": "mcp__playwright__browser_take_screenshot",
                   "input": {}})
        out({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_p",
             "content": [{"type": "text", "text": "Took a screenshot"},
                         {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png}},
                         {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                      "data": "not base64!"}}]}]},
             "parent_tool_use_id": None, "session_id": SID})
        result("Looked at the design.")
        drain()
    elif SCENARIO == "resume":
        result("resumed")
        drain()


if __name__ == "__main__":
    main()
