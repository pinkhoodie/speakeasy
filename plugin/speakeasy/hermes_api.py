"""Client for the user's Hermes API server on loopback (/v1/runs, events, stop, steer, approval)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .text import ID_RE, TERMINAL

MAX_BODY = 512 * 1024
RUN_EVENTS_TIMEOUT_S = 1800


class HermesError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _loopback(base: str) -> str:
    """Only this machine's own addresses: loopback, or an interface address such as a Tailscale IP
    that the API server is bound to (traffic to it stays on this machine)."""
    from .settings import is_this_machine
    parsed = urllib.parse.urlsplit(base)
    host = parsed.hostname or ""
    if parsed.scheme != "http" or not (host in {"127.0.0.1", "::1", "localhost"} or is_this_machine(host)):
        raise ValueError("Hermes API must be an http origin on this machine")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Hermes API origin must not contain credentials/query/fragment")
    return base.rstrip("/")


class HermesAPI:
    """`key_fn` is called per request so a key written by `hermes voice setup` is picked up
    without a restart; the key is never logged or returned."""

    def __init__(self, base: str, key_fn: Callable[[], str], profile: str = "",
                 opener: Callable[..., Any] = urllib.request.urlopen):
        root = _loopback(base)
        self.base = f"{root}/p/{profile}" if profile else root
        self.key_fn, self.opener = key_fn, opener
        self.last_error = ""  # Hermes' own message for the last failed background run

    def _request(self, path: str, body: dict[str, Any] | None = None, method: str | None = None,
                 headers: dict[str, str] | None = None) -> urllib.request.Request:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method or ("POST" if data else "GET"))
        key = self.key_fn()
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        return req

    def _json(self, method: str, path: str, body: dict[str, Any] | None = None,
              headers: dict[str, str] | None = None, timeout: float = 30) -> dict[str, Any]:
        req = self._request(path, body, method, headers)
        try:
            with self.opener(req, timeout=timeout) as response:
                raw = response.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise HermesError(502, "Hermes response too large")
                data = json.loads(raw or b"{}")
                return data if isinstance(data, dict) else {}
        except urllib.error.HTTPError as exc:
            raise HermesError(502 if exc.code >= 500 else exc.code, f"Hermes HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HermesError(502, f"Hermes unavailable: {type(exc).__name__}") from None
        except ValueError:
            raise HermesError(502, "Hermes returned invalid JSON") from None

    def health(self, timeout: float = 2) -> bool:
        try:
            self._json("GET", "/health", timeout=timeout)
            return True
        except HermesError:
            return False

    def start_run(self, prompt: str, idem_key: str, session_id: str | None = None,
                  session_key: str | None = None) -> str:
        body: dict[str, Any] = {"input": prompt}
        if session_id:
            # A task's own Hermes session: tasks in separate sessions run at the same time.
            body["session_id"] = session_id
        headers = {"Idempotency-Key": idem_key}
        if session_key:
            headers["X-Hermes-Session-Key"] = session_key
        result = self._json("POST", "/v1/runs", body, headers)
        run_id = result.get("run_id")
        if not isinstance(run_id, str) or not ID_RE.fullmatch(run_id):
            raise HermesError(502, "Hermes returned an invalid run ID")
        return run_id

    def events(self, run_id: str, callback: Callable[[dict[str, Any]], None]) -> bool:
        """Stream one run's events; True when a terminal event arrived."""
        if not ID_RE.fullmatch(run_id):
            raise HermesError(400, "invalid run ID")
        req = self._request(f"/v1/runs/{run_id}/events")
        try:
            with self.opener(req, timeout=RUN_EVENTS_TIMEOUT_S) as response:
                total, terminal = 0, False
                for raw in response:
                    total += len(raw)
                    if total > 4 * 1024 * 1024:
                        raise HermesError(502, "Hermes event stream too large")
                    if raw.startswith(b"data: "):
                        try:
                            event = json.loads(raw[6:])
                        except ValueError:
                            continue
                        if isinstance(event, dict):
                            callback(event)
                            terminal = terminal or event.get("event") in {f"run.{s}" for s in TERMINAL}
                return terminal
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HermesError(502, f"Hermes event stream failed: {type(exc).__name__}") from None

    def get_run(self, run_id: str) -> dict[str, Any]:
        if not ID_RE.fullmatch(run_id):
            raise HermesError(400, "invalid run ID")
        return self._json("GET", f"/v1/runs/{run_id}")

    def stop(self, run_id: str) -> dict[str, Any]:
        if not ID_RE.fullmatch(run_id):
            raise HermesError(400, "invalid run ID")
        return self._json("POST", f"/v1/runs/{run_id}/stop", {})

    def steer(self, run_id: str, text: str) -> bool:
        """Add guidance to a run that is still working; False if it no longer accepts it."""
        if not ID_RE.fullmatch(run_id):
            raise HermesError(400, "invalid run ID")
        try:
            return bool(self._json("POST", f"/v1/runs/{run_id}/steer", {"input": text}).get("accepted"))
        except HermesError:
            return False

    def approve(self, run_id: str, request_id: str, choice: str) -> dict[str, Any]:
        if choice not in {"once", "deny"}:
            raise HermesError(400, "approval choice must be once or deny")
        return self._json("POST", f"/v1/runs/{run_id}/approval", {"request_id": request_id, "choice": choice})

    def session_messages(self, session_id: str, limit: int = 60, timeout: float = 1.5) -> list[dict[str, Any]]:
        data = self._json("GET", f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/messages"
                                 f"?limit={limit}&order=latest", timeout=timeout)
        messages = data.get("data") or []
        return [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []

    def run_to_completion(self, prompt: str, idem_key: str, session_id: str | None = None) -> tuple[str, str]:
        """Start a run and wait for its terminal event: (status, output). Used for background jobs."""
        run_id = self.start_run(prompt, idem_key, session_id)
        final: dict[str, Any] = {}

        def on_event(event: dict[str, Any]) -> None:
            kind = event.get("event") or ""
            if kind.startswith("run.") and kind[4:] in TERMINAL:
                final["status"] = kind[4:]
                if isinstance(event.get("output"), str):
                    final["output"] = event["output"]

        if not self.events(run_id, on_event) or "output" not in final or final.get("status") != "completed":
            result = self.get_run(run_id)
            final.setdefault("status", result.get("status") or "unknown")
            if isinstance(result.get("output"), str):
                final.setdefault("output", result["output"])
            if final.get("status") != "completed" and isinstance(result.get("error"), str):
                self.last_error = result["error"]
        return final.get("status", "unknown"), final.get("output", "")
