"""Test doubles: a fake Hermes API server (real HTTP on loopback), a fake Codex app-server
(stdio JSON-RPC in a subprocess), and a fake live-call worker. No network, no paid calls."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HERMES_SRC = Path(os.environ.get("HERMES_AGENT_SRC", Path.home() / ".hermes" / "hermes-agent"))
for p in (str(PLUGIN_ROOT), str(HERMES_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from speakeasy.calls import SidebandWorker  # noqa: E402

SDP = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
FAKE_API_KEY = "test-api-server-key-not-real"


class FakeHermes(BaseHTTPRequestHandler):
    """Implements /health, /v1/runs, /v1/runs/{id}, /events, /stop, /steer, /approval.

    ``responder(prompt, session_id)`` decides each run's final output (default: echo)."""

    def log_message(self, *a: Any) -> None:
        pass

    @property
    def state(self) -> "FakeHermesServer":
        return self.server.state  # type: ignore[attr-defined]

    def _json(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authed(self) -> bool:
        if self.headers.get("Authorization") != f"Bearer {FAKE_API_KEY}":
            self._json(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok"})
            return
        if not self._authed():
            return
        parts = self.path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["v1", "runs"] and parts[3] == "events":
            run = self.state.runs.get(parts[2])
            if not run:
                self._json(404, {"error": "no run"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in run.get("pre_events", []):
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                self.wfile.flush()
            run["done"].wait(10)
            for event in run["events"]:
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.flush()
            return
        if len(parts) == 3 and parts[:2] == ["v1", "runs"]:
            run = self.state.runs.get(parts[2])
            if not run:
                self._json(404, {"error": "no run"})
                return
            status = {"run_id": parts[2], "status": run["status"], "output": run["output"]}
            if run.get("error"):
                status["error"] = run["error"]
            self._json(200, status)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._authed():
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        parts = self.path.strip("/").split("/")
        if parts == ["v1", "runs"]:
            idem = self.headers.get("Idempotency-Key", "")
            with self.state.lock:
                if idem and idem in self.state.by_idem:
                    self._json(202, {"run_id": self.state.by_idem[idem], "status": "started"})
                    return
                self.state.counter += 1
                run_id = f"run_{self.state.counter:04d}"
                self.state.by_idem[idem] = run_id
            prompt, session_id = body.get("input", ""), body.get("session_id")
            self.state.calls.append({"run_id": run_id, "input": prompt, "session_id": session_id, "idem": idem})
            output = self.state.responder(prompt, session_id)
            run = {"status": "completed", "output": output, "done": threading.Event(),
                   "events": [{"event": "tool.started", "tool": "web_search", "preview": "weather"},
                              {"event": "run.completed", "output": output}]}
            if self.state.fail_with is not None:
                # Hermes' shape for a turn that failed (api_server_runs._execute_run): no output, the
                # provider's redacted error line in ``error`` on the event and on GET /v1/runs/{id}.
                # ``fail_reply`` instead gives the session chat stream's shape: the reply is Hermes'
                # failure message and there is no error field.
                reply = self.state.fail_reply
                run.update(status="failed", output=reply or "", error=None if reply else self.state.fail_with,
                           events=[{"event": "run.failed", "completed": False, "partial": False,
                                    "interrupted": False, **({"output": reply} if reply else
                                                             {"error": self.state.fail_with})}])
            if self.state.drop_terminal:
                run["events"] = [e for e in run["events"] if not e["event"].startswith("run.")]
            if self.state.approval_first:
                run["pre_events"] = [{"event": "approval.request", "request_id": "apr_" + run_id,
                                      "description": "Delete an old file"}]
            if self.state.live_events:
                run["pre_events"] = run.get("pre_events", []) + list(self.state.live_events)
            if self.state.hold or self.state.approval_first:
                run["status"] = "running"
            else:
                run["done"].set()
            self.state.runs[run_id] = run
            self._json(202, {"run_id": run_id, "status": "started"})
            return
        if len(parts) == 4 and parts[:2] == ["v1", "runs"]:
            run = self.state.runs.get(parts[2])
            if not run:
                self._json(404, {"error": "no run"})
                return
            if parts[3] == "stop":
                run["status"] = "cancelled"
                run["events"] = [{"event": "run.cancelled"}]
                run["done"].set()
                self._json(200, {"status": "stopping"})
            elif parts[3] == "steer":
                self.state.steers.append((parts[2], body.get("input", "")))
                self._json(200, {"accepted": run["status"] == "running"})
            elif parts[3] == "approval":
                self.state.approvals.append(body)
                run["status"] = "completed"
                run["done"].set()
                self._json(200, {"resolved": 1})
            else:
                self._json(404, {"error": "not found"})
            return
        self._json(404, {"error": "not found"})


class FakeHermesServer:
    def __init__(self, responder: Callable[[str, str | None], str] | None = None):
        self.lock = threading.Lock()
        self.runs: dict[str, dict[str, Any]] = {}
        self.by_idem: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []
        self.approvals: list[dict[str, Any]] = []
        self.counter = 0
        self.hold = False
        self.approval_first = False
        self.live_events: list[dict[str, Any]] = []  # streamed while a held run is still working
        self.steers: list[tuple[str, str]] = []
        self.fail_with: str | None = None  # every run fails with this Hermes error line
        self.fail_reply: str | None = None  # …or with this failure reply and no error (session chat shape)
        self.drop_terminal = False          # the event stream ends without its run.* event
        self.responder = responder or (lambda prompt, sid: "Done.\nDONE: Finished\nSPOKEN: All done.")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeHermes)
        self.httpd.daemon_threads = True
        self.httpd.state = self  # type: ignore[attr-defined]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def make_home(api_port: int | None = None) -> Path:
    home = Path(tempfile.mkdtemp(prefix="speakeasy-test-"))
    env = [f"API_SERVER_KEY={FAKE_API_KEY}", "SPEAKEASY_OPENAI_API_KEY=test-openai-key-not-real"]
    if api_port:
        env.append(f"API_SERVER_PORT={api_port}")
    (home / ".env").write_text("\n".join(env) + "\n")
    return home


class FakeTransport:
    """Stands in for CodexTransport in service tests (no subprocess)."""

    def __init__(self) -> None:
        self.thread_id = "thr_fake"
        self.closed = False
        self.started_at = time.monotonic()
        self.max_call_s = 60
        self.requests: list[tuple[str, dict]] = []
        self.started: tuple | None = None

    def start(self, sdp: str, instructions: str, seed: list, voice: str) -> str:
        self.started = (sdp, instructions, seed, voice)
        return "v=0\r\nanswer\r\n"

    def request(self, method: str, params: dict | None = None, timeout: float = 20) -> dict:
        self.requests.append((method, params or {}))
        return {}

    def stop(self) -> None:
        self.closed = True


class FakeLiveWorker(SidebandWorker):
    """Live-call worker whose provider is the test: it records everything sent to the call."""

    def __init__(self, rt: Any, interaction: Any):
        super().__init__(rt, interaction)
        self.sent: list[tuple[str, str | None, str]] = []
        self.ready = threading.Event()
        self.connected = True

    def start(self) -> None:
        def runner() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.ready.set()
            self.loop.run_forever()
        threading.Thread(target=runner, daemon=True).start()
        self.ready.wait(5)

    async def _send(self, kind: str, delegation_id: str | None, content: str) -> None:
        self.sent.append((kind, delegation_id, content))

    def feed(self, event: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(self.handle_event(event), self.loop).result(10)  # type: ignore[arg-type]

    def delegate(self, delegation_id: str, words: str, **extra: Any) -> None:
        self.feed({"type": "session.input_transcript.delta", "delta": words, "start_ms": 1, "end_ms": 2})
        self.feed({"type": "session.delegation.created", "offset_ms": 5,
                   "delegation": {"id": delegation_id, "target": "client", **extra}})


def wait_for(predicate: Callable[[], Any], timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def http(base: str, method: str, path: str, body: Any = None, token: str | None = None,
         headers: dict[str, str] | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
            return r.status, json.loads(raw) if "json" in ctype else raw
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
