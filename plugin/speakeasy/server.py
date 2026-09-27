"""HTTP layer for the Speakeasy Mac app: the /voice/* routes, SSE events, pairing.

Loopback only. Every route except ``GET /health`` and ``POST /voice/pair`` needs
``Authorization: Bearer <device token>`` from ``devices.py``. Shapes: docs/API.md.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .service import VoiceService
from .calls import ServiceError, Interaction, publish_state

logger = logging.getLogger(__name__)

MAX_BODY = 128 * 1024
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_ID = r"([A-Za-z0-9_-]+)"


class Handler(BaseHTTPRequestHandler):
    server_version = f"Speakeasy/{__version__}"
    heartbeat_s = 15.0

    @property
    def service(self) -> VoiceService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # never log tokens or bodies
        logger.debug("speakeasy http: " + fmt, *args)

    # -- plumbing ---------------------------------------------------------------------------
    def _reply(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ServiceError(400, "invalid content length") from None
        if length <= 0 or length > MAX_BODY:
            raise ServiceError(413, "request body size rejected")
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            raise ServiceError(400, "invalid JSON") from None
        if not isinstance(body, dict):
            raise ServiceError(400, "JSON object required")
        return body

    @property
    def route(self) -> str:
        return urlsplit(self.path).path

    def _auth(self) -> str:
        return self.service.authenticate(self.headers)

    def _sse(self, seq: int, kind: str, payload: Any) -> None:
        data = json.dumps(payload, separators=(",", ":"))
        self.wfile.write(f"id: {seq}\nevent: {kind}\ndata: {data}\n\n".encode())
        self.wfile.flush()

    def _events(self, interaction: Interaction) -> None:
        """Live SSE stream: snapshot (or resume via Last-Event-ID), deduped changes, heartbeat, closed."""
        store, feed = self.service.store, interaction.feed
        name = self.service.settings.get()["assistant_name"]
        try:
            cursor = int(self.headers.get("Last-Event-ID", ""))
        except ValueError:
            cursor = -1
        publish_state(store, interaction, name)
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def snapshot() -> tuple[int, dict[str, Any]]:
            seq = feed.seq - 1 if feed.closed_id is not None else feed.seq
            return seq, {"interaction": feed.last["interaction"] or interaction.snapshot(),
                         "work": feed.last["work"], "approval": feed.last["approval"],
                         "tasks": feed.last["tasks"] or [], "email_drafts": feed.last.get("email_drafts") or [],
                         "away": interaction.away}
        try:
            with feed.cond:
                pending = feed.since(cursor) if cursor >= 0 else None
                first: list[tuple[int, str, Any]] = []
                if pending is None:
                    cursor, body = snapshot()
                    first = [(cursor, "snapshot", body)]
            for seq, kind, payload in first:
                self._sse(seq, kind, payload)
            last_write = time.monotonic()
            while True:
                with feed.cond:
                    if feed.closed_id is not None and cursor >= feed.closed_id:
                        return
                    remaining = self.heartbeat_s - (time.monotonic() - last_write)
                    feed.cond.wait_for(lambda: feed.seq > cursor, timeout=max(0.0, remaining))
                    batch = feed.since(cursor)
                    if batch is None:
                        cursor, body = snapshot()
                        batch = [(cursor, "snapshot", body)]
                for seq, kind, payload in batch:
                    self._sse(seq, kind, payload)
                    cursor = max(cursor, seq)
                    last_write = time.monotonic()
                if time.monotonic() - last_write >= self.heartbeat_s:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_write = time.monotonic()
                    publish_state(store, interaction, name)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return

    # -- routes -------------------------------------------------------------------------------
    def do_GET(self) -> None:
        path = self.route
        try:
            if path == "/health":
                self._reply(200, {"ok": True, "platform": "voice", "version": __version__,
                                  "provider": self.service.settings.get()["voice"]["provider"]})
                return
            self._auth()
            image = re.fullmatch(rf"/voice/card-image/{_ID}/([1-8])", path)
            if image:
                data, mime = self.service.card_image(image.group(1), int(image.group(2)))
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "private, max-age=300")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            events = re.fullmatch(rf"/voice/interactions/{_ID}/events", path)
            if events:
                self._events(self.service.interaction(events.group(1)))
                return
            match = re.fullmatch(rf"/voice/interactions/{_ID}", path)
            work = re.fullmatch(rf"/voice/work/{_ID}", path)
            routes = {
                "/voice/work/latest": self.service.work_latest,
                "/voice/settings": self.service.get_settings,
                "/voice/brief": self.service.get_brief,
                "/voice/status": self.service.status,
                "/voice/destinations": self.service.destinations,
                "/voice/onboarding": self.service.onboarding,
            }
            if path in routes:
                self._reply(200, routes[path]())
            elif work:
                self._reply(200, self.service.work(work.group(1)))
            elif match:
                self._reply(200, self.service.interaction(match.group(1)).snapshot())
            else:
                raise ServiceError(404, "not found")
        except ServiceError as exc:
            self._reply(exc.status, {"error": exc.message})
        except Exception as exc:
            logger.exception("speakeasy: GET %s failed", path)
            self._reply(500, {"error": f"internal error ({type(exc).__name__})"})

    def do_POST(self) -> None:
        path = self.route
        try:
            if path == "/voice/pair":
                self._reply(201, self.service.pair(self._body()))
                return
            device_id = self._auth()
            if path == "/voice/sessions":
                self._reply(201, self.service.create_session(self._body(), self.headers.get("Idempotency-Key", ""),
                                                            device_id))
                return
            if path == "/voice/tasks/dismiss":
                self._reply(200, self.service.dismiss_tasks(self._body()))
                return
            if path == "/voice/brief/rewrite":
                self._reply(202, self.service.rewrite_brief())
                return
            if path == "/voice/onboarding":
                self._reply(200, self.service.save_onboarding(self._body()))
                return
            draft = re.fullmatch(rf"/voice/drafts/{_ID}", path)
            if draft:
                self._reply(200, self.service.decide_draft(draft.group(1), self._body()))
                return
            action = re.fullmatch(rf"/voice/interactions/{_ID}/(end|pause|approval|cancel-backend)", path)
            if not action:
                raise ServiceError(404, "not found")
            interaction_id, verb = action.groups()
            if verb == "end":
                self._reply(200, self.service.finish_transport(interaction_id))
            elif verb == "pause":
                self._reply(200, self.service.pause(interaction_id))
            elif verb == "cancel-backend":
                self._reply(202, self.service.cancel_backend(interaction_id, self._body()))
            else:
                self._reply(200, self.service.approve(interaction_id, self._body()))
        except ServiceError as exc:
            self._reply(exc.status, {"error": exc.message})
        except Exception as exc:
            logger.exception("speakeasy: POST %s failed", path)
            self._reply(500, {"error": f"internal error ({type(exc).__name__})"})

    def do_PATCH(self) -> None:
        try:
            self._auth()
            if self.route != "/voice/settings":
                raise ServiceError(404, "not found")
            self._reply(200, self.service.patch_settings(self._body()))
        except ServiceError as exc:
            self._reply(exc.status, {"error": exc.message})

    def do_PUT(self) -> None:
        try:
            self._auth()
            if self.route != "/voice/brief":
                raise ServiceError(404, "not found")
            self._reply(200, self.service.put_brief(self._body()))
        except ServiceError as exc:
            self._reply(exc.status, {"error": exc.message})


class SpeakeasyServer:
    """Owns the ThreadingHTTPServer and the VoiceService. Bound to loopback only."""

    def __init__(self, service: VoiceService, host: str = "127.0.0.1", port: int = 8795):
        if host not in LOOPBACK:
            raise ValueError("Speakeasy binds loopback only; put a TLS proxy (e.g. Tailscale serve) in front")
        self.service = service
        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.httpd.service = service  # type: ignore[attr-defined]
        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="speakeasy-http", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.service.close()
