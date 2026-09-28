"""Codex as a task backend: one long-lived ``codex app-server`` child speaking JSON-RPC lines on stdio.

This is separate from the realtime voice transport in ``codex_transport.py``: the task backend owns
its own app-server process, started lazily in the user's chosen workspace and restarted if it dies.

Mapping (codex-cli 0.144 app-server v2, stable surface only):

- ``start_run``   → ``thread/start`` (or ``thread/resume`` for a ``session_id``) then ``turn/start``
- ``steer``       → ``turn/steer`` (with ``expectedTurnId``)
- ``stop``        → ``turn/interrupt``
- ``session_messages`` → ``thread/read`` with ``includeTurns``
- server requests ``item/commandExecution/requestApproval``, ``item/fileChange/requestApproval``,
  ``item/permissions/requestApproval`` (and legacy ``execCommandApproval`` / ``applyPatchApproval``)
  become ``approval.request`` events answered through ``approve``.

Every notification is translated into the contract vocabulary in ``base.py``; nothing else leaks.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..text import (EMAIL_RE, HOME_PATH_RE, ID_RE, OPAQUE_RE, SECRET_FILE_RE, SENSITIVE_TEXT_RE, TERMINAL,
                    URL_SECRET_RE)
from .base import APPROVAL_CHOICES, BackendError, Capabilities, EventCallback

RPC_TIMEOUT_S = 30.0
RUN_EVENTS_TIMEOUT_S = 1800
MAX_DIFF = 64 * 1024
MAX_OUTPUT = 256 * 1024          # final answer text kept per run
MAX_INTERIM = 2000
MAX_EVENTS = 4000                # per-run event log bound
MAX_LINE = 8 * 1024 * 1024       # one JSON-RPC line from the child
SANDBOX_MODES = frozenset({"read-only", "workspace-write", "danger-full-access"})
APPROVAL_POLICIES = frozenset({"untrusted", "on-request", "never"})
# The child needs its own ChatGPT/Codex auth and a working toolchain, never Hermes or API-key vars.
_ENV_KEEP = ("HOME", "PATH", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
             "TMPDIR", "SSL_CERT_FILE", "CODEX_HOME")
# Chatty notifications Speakeasy never uses; opting out keeps the pipe quiet.
_OPT_OUT = ["item/commandExecution/outputDelta", "item/fileChange/outputDelta", "item/reasoning/textDelta",
            "item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded", "item/plan/delta",
            "command/exec/outputDelta", "process/outputDelta", "thread/tokenUsage/updated",
            "account/rateLimits/updated", "rawResponseItem/completed"]
_APPROVAL_METHODS = {
    "item/commandExecution/requestApproval": "command",
    "item/fileChange/requestApproval": "file_change",
    "item/permissions/requestApproval": "permission",
    "execCommandApproval": "command",
    "applyPatchApproval": "file_change",
}


def child_env() -> dict[str, str]:
    return {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}


def _default_spawn(argv: list[str], cwd: str, env: dict[str, str]) -> Any:
    return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, bufsize=1, cwd=cwd, env=env)


def _unsafe(text: str) -> bool:
    return bool(SENSITIVE_TEXT_RE.search(text) or EMAIL_RE.search(text) or URL_SECRET_RE.search(text)
                or OPAQUE_RE.search(text) or SECRET_FILE_RE.search(text) or "```" in text)


_SHELL_WRAP_RE = re.compile(r"^(?:/[\w/]+/)?(?:zsh|bash|sh)\s+-l?c\s+(?P<q>['\"])(?P<cmd>.*)(?P=q)$", re.S)


def _unwrap_shell(command: str) -> str:
    """``/bin/zsh -lc 'npm test'`` → ``npm test`` (Codex wraps every command in a login shell)."""
    match = _SHELL_WRAP_RE.match(command.strip())
    return match.group("cmd") if match else command


def _rpc_error_message(error: Any) -> str:
    message = error.get("message") if isinstance(error, dict) else None
    # Provider errors often arrive as a JSON document inside ``message``; surface its own message.
    for _ in range(2):
        try:
            inner = json.loads(message) if isinstance(message, str) and message.lstrip().startswith("{") else None
        except ValueError:
            inner = None
        if not isinstance(inner, dict):
            break
        nested = inner.get("error") if isinstance(inner.get("error"), dict) else inner
        message = nested.get("message") if isinstance(nested.get("message"), str) else message
    text = re.sub(r"\s+", " ", message).strip() if isinstance(message, str) else ""
    return "Codex error: " + text[:200] if text and not _unsafe(text) else "Codex request failed"


@dataclass
class _Run:
    run_id: str
    idem_key: str
    thread_id: str | None = None
    turn_id: str | None = None
    status: str = "starting"          # starting | running | waiting_for_approval | TERMINAL
    output: str | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    started_emitted: bool = False
    stop_requested: bool = False
    final_parts: list[str] = field(default_factory=list)     # phase == final_answer
    unknown_parts: list[str] = field(default_factory=list)   # phase unknown (legacy models)
    output_size: int = 0
    deltas: dict[str, str] = field(default_factory=dict)     # itemId → streamed text (fallback)
    completed_items: set[str] = field(default_factory=set)
    file_items: dict[str, list[str]] = field(default_factory=dict)  # fileChange itemId → paths
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)  # request_id → pending server req
    last_diff: str | None = None
    generation: int = 0

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL


class CodexBackend:
    """A ``TaskBackend`` driving ``codex app-server`` in one workspace directory."""

    def __init__(self, workspace: str | Path, *, codex_bin: str = "codex", model: str | None = None,
                 sandbox: str = "workspace-write", approval_policy: str = "on-request",
                 spawn: Callable[[list[str], str, dict[str, str]], Any] = _default_spawn):
        path = Path(workspace).expanduser()
        if not path.is_dir():
            raise BackendError(400, "Codex workspace must be an existing folder")
        if sandbox not in SANDBOX_MODES:
            raise BackendError(400, f"unknown sandbox mode {sandbox!r}")
        if approval_policy not in APPROVAL_POLICIES:
            raise BackendError(400, f"unknown approval policy {approval_policy!r}")
        self.workspace = path.resolve()
        self.codex_bin, self.model = codex_bin, model
        self.sandbox, self.approval_policy = sandbox, approval_policy
        self.spawn = spawn
        self.last_error = ""
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._write_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._proc: Any = None
        self._generation = 0
        self._seq = 0
        self._pending: dict[int, dict[str, Any]] = {}     # rpc id → {"done": Event, "msg": dict}
        self._runs: dict[str, _Run] = {}
        self._by_idem: dict[str, str] = {}
        self._by_thread: dict[str, str] = {}               # threadId → active run_id
        self._loaded_threads: set[str] = set()             # threads live in the current child

    # ------------------------------------------------------------------ contract: metadata

    def capabilities(self) -> Capabilities:
        return Capabilities(kind="codex", display_name="Codex", steer=True, approvals=True, file_changes=True,
                            needs_workspace=True, extras={"workspace": str(self.workspace)})

    def health(self, timeout: float = 2) -> bool:
        try:
            self._ensure(timeout=timeout)
            return True
        except BackendError:
            return False

    # ------------------------------------------------------------------ child process + JSON-RPC

    def _ensure(self, timeout: float = RPC_TIMEOUT_S) -> None:
        """Start (or restart) the child and run the ``initialize`` handshake."""
        with self._start_lock:
            with self._lock:
                proc = self._proc
            if proc is not None and proc.poll() is None:
                return
            argv = [self.codex_bin, "app-server", "--stdio"]
            try:
                proc = self.spawn(argv, str(self.workspace), child_env())
            except OSError as exc:
                raise BackendError(502, f"Could not start Codex ({type(exc).__name__})") from None
            with self._lock:
                self._generation += 1
                generation = self._generation
                self._proc = proc
                self._loaded_threads = set()
            threading.Thread(target=self._reader, args=(proc, generation), daemon=True,
                             name="speakeasy-codex-task-reader").start()
            try:
                self._request("initialize", {
                    "clientInfo": {"name": "speakeasy", "title": "Speakeasy", "version": "0.1"},
                    "capabilities": {"experimentalApi": False, "requestAttestation": False,
                                     "optOutNotificationMethods": _OPT_OUT}}, timeout=timeout, proc=proc)
                self._send({"method": "initialized"}, proc=proc)
            except BackendError:
                self._kill(proc)
                raise

    def _kill(self, proc: Any) -> None:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except (OSError, AttributeError):
            pass

    def close(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is not None:
            self._kill(proc)

    def _send(self, message: dict[str, Any], proc: Any = None) -> None:
        with self._lock:
            proc = proc or self._proc
        if proc is None or proc.poll() is not None:
            raise BackendError(502, "Codex app-server is not running")
        try:
            with self._write_lock:
                proc.stdin.write(json.dumps(message) + "\n")
                proc.stdin.flush()
        except (OSError, ValueError):
            raise BackendError(502, "Codex app-server is not running") from None

    def _request(self, method: str, params: dict[str, Any], timeout: float = RPC_TIMEOUT_S,
                 proc: Any = None) -> dict[str, Any]:
        waiter = {"done": threading.Event(), "msg": None}
        with self._lock:
            self._seq += 1
            number = self._seq
            self._pending[number] = waiter
        try:
            self._send({"id": number, "method": method, "params": params}, proc=proc)
            if not waiter["done"].wait(timeout):
                raise BackendError(502, f"Codex {method} timed out")
            message = waiter["msg"] or {}
            if "error" in message:
                raise BackendError(502, _rpc_error_message(message.get("error")))
            result = message.get("result")
            return result if isinstance(result, dict) else {}
        finally:
            with self._lock:
                self._pending.pop(number, None)

    def _reader(self, proc: Any, generation: int) -> None:
        try:
            for line in proc.stdout:
                if len(line) > MAX_LINE:
                    continue
                try:
                    message = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(message, dict):
                    continue
                try:
                    self._dispatch(message)
                except Exception:  # noqa: BLE001 - one bad message must not kill the reader
                    continue
        except (OSError, ValueError):
            pass
        finally:
            self._child_gone(proc, generation)

    def _child_gone(self, proc: Any, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._proc = None
            self._loaded_threads = set()
            for waiter in self._pending.values():
                waiter["msg"] = {"error": {"message": "app-server exited"}}
                waiter["done"].set()
            for run in self._runs.values():
                if not run.terminal and run.generation == generation:
                    self._finish(run, "failed", error="Codex app-server exited")
        self._kill(proc)

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is None:
            with self._lock:
                waiter = self._pending.get(message.get("id"))  # type: ignore[arg-type]
                if waiter is not None:
                    waiter["msg"] = message
                    waiter["done"].set()
            return
        raw = message.get("params")
        params: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if "id" in message:
            self._server_request(message["id"], method, params)
        else:
            self._notification(method, params)

    # ------------------------------------------------------------------ run bookkeeping

    def _emit(self, run: _Run, event: dict[str, Any]) -> None:
        """Append to the run's log (caller holds the lock) and wake ``events`` readers."""
        if len(run.events) >= MAX_EVENTS and not event["event"].startswith("run."):
            return
        if event["event"] == "files.changed":
            # Older snapshots are superseded; drop their diff so the log stays small.
            for i, old in enumerate(run.events):
                if old.get("event") == "files.changed" and old.get("diff"):
                    run.events[i] = {**old, "diff": ""}
        run.events.append(event)
        self._cond.notify_all()

    def _finish(self, run: _Run, status: str, output: str | None = None, error: str | None = None) -> None:
        if run.terminal:
            return
        run.status, run.output, run.error = status, output, error
        run.approvals.clear()
        if run.thread_id and self._by_thread.get(run.thread_id) == run.run_id:
            self._by_thread.pop(run.thread_id, None)
        if status != "completed" and error:
            self.last_error = error
        event: dict[str, Any] = {"event": f"run.{status}", "run_id": run.run_id}
        if output is not None:
            event["output"] = output
        if error:
            event["error"] = error
        self._emit(run, event)

    def _run_for(self, params: dict[str, Any]) -> _Run | None:
        thread_id = params.get("threadId") or params.get("conversationId")
        if not isinstance(thread_id, str):
            return None
        run = self._runs.get(self._by_thread.get(thread_id, ""))
        if run is None or run.terminal:
            return None
        turn_id = params.get("turnId")
        if isinstance(turn_id, str) and run.turn_id and turn_id != run.turn_id:
            return None
        return run

    def _mark_started(self, run: _Run, turn_id: Any) -> None:
        if isinstance(turn_id, str) and not run.turn_id:
            run.turn_id = turn_id
        if run.status == "starting":
            run.status = "running"
        if not run.started_emitted:
            run.started_emitted = True
            self._emit(run, {"event": "run.started", "run_id": run.run_id})

    def _add_output(self, run: _Run, text: str, final: bool) -> None:
        if run.output_size >= MAX_OUTPUT:
            return
        text = text[:MAX_OUTPUT - run.output_size]
        run.output_size += len(text)
        (run.final_parts if final else run.unknown_parts).append(text)

    def _final_output(self, run: _Run) -> str:
        # Codex's final answer is the turn's last agent message; with a known phase use exactly
        # the final_answer messages, otherwise the last message (like `codex exec`).
        parts = run.final_parts or run.unknown_parts[-1:]
        if not parts:
            parts = [t for t in run.deltas.values() if t.strip()][-1:]
        return "\n\n".join(p.strip() for p in parts if p.strip())

    # ------------------------------------------------------------------ notifications

    def _notification(self, method: str, params: dict[str, Any]) -> None:
        with self._lock:
            run = self._run_for(params)
            if run is None:
                return
            if method == "turn/started":
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                self._mark_started(run, turn.get("id"))
            elif method == "item/started":
                self._item_started(run, params.get("item"))
            elif method == "item/completed":
                self._item_completed(run, params.get("item"))
            elif method == "item/agentMessage/delta":
                item_id, delta = params.get("itemId"), params.get("delta")
                if isinstance(item_id, str) and isinstance(delta, str) and item_id not in run.completed_items:
                    current = run.deltas.get(item_id, "")
                    if len(current) < MAX_OUTPUT:
                        run.deltas[item_id] = current + delta
            elif method == "turn/diff/updated":
                self._diff(run, params.get("diff"))
            elif method == "serverRequest/resolved":
                request = params.get("requestId")
                for rid, pending in list(run.approvals.items()):
                    if pending["rpc_id"] == request:
                        run.approvals.pop(rid, None)
                if not run.approvals and run.status == "waiting_for_approval":
                    run.status = "running"
            elif method == "error":
                error = params.get("error") if isinstance(params.get("error"), dict) else {}
                if not params.get("willRetry"):
                    run.error = _rpc_error_message(error)
            elif method == "turn/completed":
                self._turn_completed(run, params.get("turn"))

    def _item_started(self, run: _Run, item: Any) -> None:
        if not isinstance(item, dict):
            return
        kind = item.get("type")
        event: dict[str, Any] | None = None
        if kind == "commandExecution":
            event = self._command_event(item)
        elif kind == "fileChange":
            paths = self._change_paths(item.get("changes"))
            if isinstance(item.get("id"), str):
                run.file_items[item["id"]] = paths
            event = {"tool": "edit_file", "preview": self._paths_preview(paths)}
        elif kind == "mcpToolCall":
            name = f"{item.get('server') or 'mcp'}.{item.get('tool') or 'tool'}"
            event = {"tool": re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:64], "preview": ""}
        elif kind == "dynamicToolCall":
            event = {"tool": re.sub(r"[^A-Za-z0-9_.-]", "_", str(item.get("tool") or "tool"))[:64], "preview": ""}
        elif kind == "webSearch":
            event = {"tool": "web_search", "preview": self._redact(item.get("query"), 200)}
        elif kind == "imageView":
            event = {"tool": "read_file", "preview": self._rel(item.get("path"))}
        elif kind == "collabAgentToolCall":
            event = {"tool": "delegate_task", "preview": ""}
        if event is not None:
            if run.status == "starting":
                self._mark_started(run, None)
            self._emit(run, {"event": "tool.started", **event})

    @staticmethod
    def _media_event(item: dict[str, Any]) -> dict[str, Any] | None:
        """``media.seen`` for an image Codex opened (imageView) or generated (imageGeneration).
        Only absolute paths; the server vets them against its image roots before serving."""
        kind = item.get("type")
        if kind == "imageView":
            path, source = item.get("path"), "viewed"
        elif kind == "imageGeneration":
            path, source = item.get("savedPath"), "generated"
        else:
            return None
        if not isinstance(path, str) or not Path(path).is_absolute():
            return None
        return {"event": "media.seen", "path": path, "name": Path(path).name[:120] or "Image", "source": source}

    def _item_completed(self, run: _Run, item: Any) -> None:
        if not isinstance(item, dict):
            return
        if item.get("type") == "fileChange" and isinstance(item.get("id"), str):
            run.file_items.setdefault(item["id"], self._change_paths(item.get("changes")))
        media = self._media_event(item)
        if media is not None:
            self._emit(run, media)
        if item.get("type") != "agentMessage":
            return
        item_id, text = item.get("id"), item.get("text")
        if isinstance(item_id, str):
            if item_id in run.completed_items:
                return
            run.completed_items.add(item_id)
            run.deltas.pop(item_id, None)
        if not isinstance(text, str) or not text.strip():
            return
        phase = item.get("phase")
        if phase == "commentary":
            self._emit(run, {"event": "message.interim", "text": text.strip()[:MAX_INTERIM]})
        else:
            self._add_output(run, text, final=phase == "final_answer")

    def _turn_completed(self, run: _Run, turn: Any) -> None:
        turn = turn if isinstance(turn, dict) else {}
        if isinstance(turn.get("id"), str) and run.turn_id and turn["id"] != run.turn_id:
            return
        for item in turn.get("items") or []:
            self._item_completed(run, item)
        status = turn.get("status")
        if status == "completed":
            self._finish(run, "completed", output=self._final_output(run))
        elif status == "interrupted":
            # text.TERMINAL has no "stopped"; an interrupted turn is a cancelled run.
            self._finish(run, "cancelled", output=self._final_output(run) or None,
                         error=None if run.stop_requested else "Codex interrupted the turn")
        else:
            error = turn.get("error") if isinstance(turn.get("error"), dict) else None
            self._finish(run, "failed", output=self._final_output(run) or None,
                         error=_rpc_error_message(error) if error else (run.error or "Codex turn failed"))

    def _diff(self, run: _Run, diff: Any) -> None:
        if not isinstance(diff, str) or diff == run.last_diff:
            return
        run.last_diff = diff
        files = parse_diff_stats(diff)
        capped = diff.encode()[:MAX_DIFF].decode(errors="ignore")
        self._emit(run, {"event": "files.changed", "files": files, "diff": capped})

    # ------------------------------------------------------------------ server requests (approvals)

    def _reply(self, rpc_id: Any, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"id": rpc_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result or {}
        try:
            self._send(message)
        except BackendError:
            pass

    def _server_request(self, rpc_id: Any, method: str, params: dict[str, Any]) -> None:
        kind = _APPROVAL_METHODS.get(method)
        if kind is None:
            # Not something a voice user can answer: decline MCP elicitations, refuse the rest.
            if method == "mcpServer/elicitation/request":
                self._reply(rpc_id, {"action": "decline", "content": None, "_meta": None})
            else:
                self._reply(rpc_id, error={"code": -32601, "message": "not supported by this client"})
            return
        with self._lock:
            run = self._run_for(params)
            if run is None:
                pending = None
            else:
                request_id = "ap_" + secrets.token_hex(12)
                description = self._describe(run, method, params)
                pending = {"rpc_id": rpc_id, "method": method, "params": params}
                run.approvals[request_id] = pending
                run.status = "waiting_for_approval"
                self._emit(run, {"event": "approval.request", "request_id": request_id,
                                 "description": description, "kind": kind})
        if pending is None:
            self._reply(rpc_id, self._decision(method, params, allow=False))  # fail closed

    def _describe(self, run: _Run, method: str, params: dict[str, Any]) -> str:
        if method in {"item/commandExecution/requestApproval", "execCommandApproval"}:
            command = params.get("command")
            if isinstance(command, list):
                command = " ".join(str(c) for c in command)
            shown = self._redact(_unwrap_shell(command) if isinstance(command, str) else command, 120)
            where = self._where(params.get("cwd"))
            return f"Run `{shown}` {where}." if shown else f"Run a command {where}."
        if method in {"item/fileChange/requestApproval", "applyPatchApproval"}:
            if method == "applyPatchApproval" and isinstance(params.get("fileChanges"), dict):
                paths = [self._rel(p) for p in params["fileChanges"]]
            else:
                paths = run.file_items.get(str(params.get("itemId")), [])
            paths = [p for p in paths if p]
            if not paths:
                return "Change files in the project."
            more = f" and {len(paths) - 1} other file{'s' if len(paths) > 2 else ''}" if len(paths) > 1 else ""
            return f"Edit {paths[0]}{more}."
        permissions = params.get("permissions") if isinstance(params.get("permissions"), dict) else {}
        asks = []
        if isinstance(permissions.get("network"), dict) and permissions["network"].get("enabled"):
            asks.append("network access")
        fs = permissions.get("fileSystem") if isinstance(permissions.get("fileSystem"), dict) else {}
        if fs.get("write") or fs.get("entries"):
            asks.append("writing outside the project")
        elif fs.get("read"):
            asks.append("reading outside the project")
        return f"Allow {' and '.join(asks) or 'extra permissions'} for this task."

    @staticmethod
    def _decision(method: str, params: dict[str, Any], allow: bool) -> dict[str, Any]:
        if method in {"execCommandApproval", "applyPatchApproval"}:
            return {"decision": "approved" if allow else "denied"}
        if method == "item/permissions/requestApproval":
            granted: dict[str, Any] = {}
            if allow:
                requested = params.get("permissions") if isinstance(params.get("permissions"), dict) else {}
                granted = {k: v for k, v in requested.items() if k in {"network", "fileSystem"} and v is not None}
            return {"permissions": granted, "scope": "turn"}
        return {"decision": "accept" if allow else "decline"}

    # ------------------------------------------------------------------ redaction helpers

    def _rel(self, path: Any) -> str:
        if not isinstance(path, str) or not path:
            return ""
        try:
            p = Path(path)
            rel = p.resolve().relative_to(self.workspace) if p.is_absolute() else p
            text = str(rel)
        except (ValueError, OSError):
            text = Path(path).name
        return "" if _unsafe(text) or HOME_PATH_RE.search(text) else text[:200]

    def _where(self, cwd: Any) -> str:
        if not isinstance(cwd, str) or not cwd:
            return "in the project"
        try:
            resolved = Path(cwd).resolve() if Path(cwd).is_absolute() else (self.workspace / cwd).resolve()
        except OSError:
            return "in the project"
        if resolved == self.workspace:
            return "in the project"
        if not resolved.is_relative_to(self.workspace):
            return "outside the project"
        rel = self._rel(str(resolved))
        return f"in {rel}/" if rel else "in the project"

    def _redact(self, text: Any, limit: int) -> str:
        if not isinstance(text, str):
            return ""
        text = re.sub(r"\s+", " ", text).strip().replace(str(self.workspace) + "/", "").replace(str(self.workspace), ".")
        text = text.replace("`", "'")
        if not text or _unsafe(text) or HOME_PATH_RE.search(text):
            return ""
        return text if len(text) <= limit else text[:limit - 1] + "…"

    def _command_event(self, item: dict[str, Any]) -> dict[str, Any]:
        actions = [a for a in item.get("commandActions") or [] if isinstance(a, dict)]
        types = {a.get("type") for a in actions}
        if actions and types == {"read"}:
            paths = [self._rel(a.get("path")) for a in actions]
            return {"tool": "read_file", "preview": self._paths_preview([p for p in paths if p])}
        if actions and types <= {"search", "listFiles", "read"}:
            return {"tool": "search_files", "preview": self._redact(
                next((a.get("query") for a in actions if a.get("query")), ""), 120)}
        command = item.get("command")
        return {"tool": "terminal", "preview": self._redact(_unwrap_shell(command) if isinstance(command, str) else "", 120)}

    def _change_paths(self, changes: Any) -> list[str]:
        paths = []
        for change in changes or []:
            if isinstance(change, dict):
                rel = self._rel(change.get("path"))
                if rel and rel not in paths:
                    paths.append(rel)
        return paths

    @staticmethod
    def _paths_preview(paths: list[str]) -> str:
        shown = ", ".join(paths[:3])
        return shown + (f" and {len(paths) - 3} more" if len(paths) > 3 else "")

    # ------------------------------------------------------------------ contract: runs

    def _get(self, run_id: str) -> _Run:
        if not isinstance(run_id, str) or not ID_RE.fullmatch(run_id):
            raise BackendError(400, "invalid run ID")
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise BackendError(404, "unknown run")
        return run

    def start_run(self, prompt: str, idem_key: str, session_id: str | None = None,
                  session_key: str | None = None) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise BackendError(400, "prompt is required")
        if not isinstance(idem_key, str) or not idem_key:
            raise BackendError(400, "idempotency key is required")
        if session_id is not None and (not isinstance(session_id, str) or not ID_RE.fullmatch(session_id)):
            raise BackendError(400, "invalid session ID")
        with self._lock:
            existing = self._by_idem.get(idem_key)
            if existing:
                return existing
            run = _Run(run_id="cx_" + secrets.token_hex(16), idem_key=idem_key)
            self._runs[run.run_id] = run
            self._by_idem[idem_key] = run.run_id
        try:
            self._ensure()
            with self._lock:
                run.generation = self._generation
                loaded = session_id in self._loaded_threads
            settings: dict[str, Any] = {"cwd": str(self.workspace), "sandbox": self.sandbox,
                                        "approvalPolicy": self.approval_policy}
            if self.model:
                settings["model"] = self.model
            if session_id and loaded:
                thread_id = session_id
            elif session_id:
                result = self._request("thread/resume", {"threadId": session_id, **settings})
                thread_id = (result.get("thread") or {}).get("id") or session_id
            else:
                result = self._request("thread/start", settings)
                thread_id = (result.get("thread") or {}).get("id")
            if not isinstance(thread_id, str) or not thread_id:
                raise BackendError(502, "Codex returned no thread")
            with self._lock:
                self._loaded_threads.add(thread_id)
                active = self._by_thread.get(thread_id)
                if active and active != run.run_id and not self._runs[active].terminal:
                    raise BackendError(409, "that Codex conversation is still working")
                run.thread_id = thread_id
                self._by_thread[thread_id] = run.run_id
            result = self._request("turn/start", {
                "threadId": thread_id, "input": [{"type": "text", "text": prompt, "text_elements": []}]})
            turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
            with self._lock:
                if not run.terminal:
                    self._mark_started(run, turn.get("id"))
                    if turn.get("status") in {"completed", "failed", "interrupted"}:
                        self._turn_completed(run, turn)
            return run.run_id
        except BackendError as exc:
            with self._lock:
                self._finish(run, "failed", error=exc.message)
                self._by_idem.pop(idem_key, None)
            raise

    def events(self, run_id: str, callback: EventCallback) -> bool:
        run = self._get(run_id)
        index, deadline = 0, time.monotonic() + RUN_EVENTS_TIMEOUT_S
        while True:
            with self._cond:
                while index >= len(run.events) and not run.terminal:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(min(1.0, remaining))
                batch = run.events[index:]
                index = len(run.events)
                done = run.terminal and index >= len(run.events)
            terminal = False
            for event in batch:
                callback(dict(event))
                terminal = terminal or event.get("event") in {f"run.{s}" for s in TERMINAL}
            if terminal or done:
                return terminal

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._get(run_id)
        with self._lock:
            status = "running" if run.status == "starting" else run.status
            result: dict[str, Any] = {"run_id": run.run_id, "status": status}
            if run.output is not None:
                result["output"] = run.output
            if run.error:
                result["error"] = run.error
            if run.thread_id:
                result["session_id"] = run.thread_id
            return result

    def stop(self, run_id: str) -> dict[str, Any]:
        run = self._get(run_id)
        with self._lock:
            if run.terminal:
                return {"run_id": run_id, "status": run.status}
            run.stop_requested = True
            thread_id, turn_id = run.thread_id, run.turn_id
        if thread_id and turn_id:
            try:
                self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=10)
                return {"run_id": run_id, "status": "stopping"}
            except BackendError:
                pass
        with self._lock:  # never started or the child is gone: nothing left to interrupt
            self._finish(run, "cancelled")
            return {"run_id": run_id, "status": run.status}

    def steer(self, run_id: str, text: str) -> bool:
        run = self._get(run_id)
        if not isinstance(text, str) or not text.strip():
            return False
        with self._lock:
            if run.terminal or not run.thread_id or not run.turn_id:
                return False
            thread_id, turn_id = run.thread_id, run.turn_id
        try:
            self._request("turn/steer", {"threadId": thread_id, "expectedTurnId": turn_id,
                                         "input": [{"type": "text", "text": text, "text_elements": []}]}, timeout=10)
            return True
        except BackendError:
            return False

    def approve(self, run_id: str, request_id: str, choice: str) -> dict[str, Any]:
        if choice not in APPROVAL_CHOICES:
            raise BackendError(400, "approval choice must be once or deny")
        run = self._get(run_id)
        with self._lock:
            pending = run.approvals.pop(request_id, None) if isinstance(request_id, str) else None
            if pending is None:
                raise BackendError(404, "no pending approval with that ID")
            if not run.approvals and run.status == "waiting_for_approval":
                run.status = "running"
        self._send({"id": pending["rpc_id"],
                    "result": self._decision(pending["method"], pending["params"], allow=choice == "once")})
        return {"run_id": run_id, "request_id": request_id, "choice": choice, "status": self.get_run(run_id)["status"]}

    def session_messages(self, session_id: str, limit: int = 60, timeout: float = 1.5) -> list[dict[str, Any]]:
        if not isinstance(session_id, str) or not ID_RE.fullmatch(session_id):
            return []
        try:
            self._ensure(timeout=max(timeout, 5))
            result = self._request("thread/read", {"threadId": session_id, "includeTurns": True}, timeout=timeout)
        except BackendError:
            return []
        messages: list[dict[str, Any]] = []
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        for turn in thread.get("turns") or []:
            for item in (turn.get("items") or []) if isinstance(turn, dict) else []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "userMessage":
                    text = "\n".join(c.get("text", "") for c in item.get("content") or []
                                     if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str))
                    if text.strip():
                        messages.append({"role": "user", "content": text})
                elif (item.get("type") == "agentMessage" and item.get("phase") != "commentary"
                      and isinstance(item.get("text"), str) and item["text"].strip()):
                    messages.append({"role": "assistant", "content": item["text"]})
        return messages[-limit:] if limit > 0 else []

    def run_to_completion(self, prompt: str, idem_key: str, session_id: str | None = None) -> tuple[str, str]:
        run_id = self.start_run(prompt, idem_key, session_id)
        final: dict[str, Any] = {}

        def on_event(event: dict[str, Any]) -> None:
            kind = event.get("event") or ""
            if kind.startswith("run.") and kind[4:] in TERMINAL:
                final["status"] = kind[4:]
                if isinstance(event.get("output"), str):
                    final["output"] = event["output"]

        self.events(run_id, on_event)
        result = self.get_run(run_id)
        final.setdefault("status", result.get("status") or "unknown")
        if isinstance(result.get("output"), str):
            final.setdefault("output", result["output"])
        if final.get("status") != "completed" and isinstance(result.get("error"), str):
            self.last_error = result["error"]
        return final.get("status", "unknown"), final.get("output", "")


def parse_diff_stats(diff: str) -> list[dict[str, Any]]:
    """``[{path, added, removed}]`` from a unified (git-style) diff."""
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    old_path: str | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            current = {"path": match.group(2) if match else line[11:], "added": 0, "removed": 0}
            files.append(current)
            old_path = None
        elif line.startswith("--- "):
            old_path = line[4:].strip()
            old_path = old_path[2:] if old_path.startswith("a/") else old_path
        elif line.startswith("+++ "):
            new_path = line[4:].strip()
            path = old_path if new_path == "/dev/null" else (new_path[2:] if new_path.startswith("b/") else new_path)
            if current is None:
                current = {"path": path, "added": 0, "removed": 0}
                files.append(current)
            elif path and path != "/dev/null":
                current["path"] = path
        elif current is not None and line.startswith("+"):
            current["added"] += 1
        elif current is not None and line.startswith("-"):
            current["removed"] += 1
    return files
