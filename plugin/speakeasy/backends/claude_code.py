"""Claude Code as a Speakeasy task backend, driven through the ``claude`` CLI's print mode.

Transport
---------
One ``claude`` process per run, in the user's workspace::

    claude -p --input-format stream-json --output-format stream-json --verbose \
        --permission-prompt-tool stdio [--permission-mode M] [--model M] \
        (--session-id <new uuid> | --resume <uuid>)

The prompt is written to stdin as a stream-json user message (never argv), stdout is one JSON
object per line. The plugin is Python-only, so this deliberately does not use the Node/TS Agent
SDK. (The Python ``claude-agent-sdk`` package is not installed in the Hermes venv; it wraps this
same CLI protocol and could replace the transport later.)

Approvals: the stdio control protocol (evidence, Claude Code 2.1.207)
----------------------------------------------------------------------
``--permission-prompt-tool`` is a hidden flag ("MCP tool to use for permission prompts (only
works with --print)", ``hideHelp()`` in the bundled CLI source). The special value ``stdio`` is
what the official SDK passes when a ``canUseTool`` callback is given
(``W.push("--permission-prompt-tool","stdio")`` in the bundle). With it the CLI writes, instead
of prompting on a TTY::

    {"type":"control_request","request_id":"…","request":{"subtype":"can_use_tool",
     "tool_name":"Bash","display_name":…,"input":{…},"tool_use_id":"…","description":…,
     "permission_suggestions":[…],"blocked_path":…}}

and blocks that tool call until it reads a matching response on stdin::

    {"type":"control_response","response":{"subtype":"success","request_id":"…",
     "response":{"behavior":"allow","updatedInput":{…}}}}           # or
     "response":{"behavior":"deny","message":"…"}}

(schema ``behavior: literal("allow"), updatedInput?`` / ``behavior: literal("deny"), message``
in the bundle). The CLI may withdraw a request with ``control_cancel_request``. A live probe
confirmed the CLI accepts ``--permission-prompt-tool stdio`` and answers our own
``control_request``s (``initialize`` and ``interrupt`` both returned ``control_response``
success; interrupt's payload is ``{"still_queued": []}``). A full can_use_tool round trip could
not be exercised live because the CLI on this Mac is logged out; the tests drive a fake CLI that
speaks exactly the shapes above.

Each ``can_use_tool`` becomes an ``approval.request`` with Speakeasy's own ``request_id`` (the
CLI's id is kept internally). ``once`` allows with the original input; ``deny`` denies with a
short message. Any other control request from the CLI (hooks, MCP bridging) is answered with an
error so the CLI never waits on us forever.

Steering
--------
``steer`` writes another stream-json user message into the open stdin. The CLI queues input it
reads while a turn is running (it reports such messages as "async user messages" in its
interrupt receipt) and folds them into the conversation at the next turn boundary, so guidance
is not seen mid-tool-call; it lands after the current model step. Semantics here: ``steer``
returns True while the run is live and stdin is open. If a turn's ``result`` arrives after a
steer was written, the run does not finish on that result: stdin is closed and the run ends on
the next ``result`` (the steered turn) or, if the CLI had already folded the steer into the
finished turn, when the process exits, using the last result.

Stop
----
``stop`` sends ``{"type":"control_request","request":{"subtype":"interrupt"}}``; if the run has
not ended within a short grace it terminates (then kills) the process. Either way the run ends
with ``run.cancelled`` (a ``text.TERMINAL`` status).

Events emitted (contract vocabulary only): ``run.started``, ``tool.started``,
``message.interim``, ``approval.request``, ``files.changed``, ``media.seen``, ``run.completed`` /
``run.failed`` / ``run.cancelled``. An auth failure arrives as a ``result`` with
``is_error: true`` (e.g. "Failed to authenticate: OAuth session expired and could not be
refreshed") and becomes ``run.failed`` with that text as ``error``. A process that dies without
a ``result`` becomes ``run.failed`` too; ``events`` never hangs on a dead process.

media.seen: a ``Read`` tool_use of an existing image file (.png/.jpg/.jpeg/.gif/.webp) emits
``{path: <absolute file>, name, source: "viewed"}``; its tool_result's inline copy is then skipped
so the image is not shown twice. Any other base64 ``image`` block inside a tool_result (browser /
Playwright MCP screenshots, a Read of a file that is gone) is written to
``$TMPDIR/speakeasy-media/<run_id>/<n>.<ext>`` (0600, capped per image and per run) and emitted
with ``source`` ``viewed`` for Read, ``generated`` for tools whose name mentions generation, else
``screenshot``.

Transcripts: the CLI stores each session at
``~/.claude/projects/<cwd with every non-alphanumeric char replaced by '-'>/<uuid>.jsonl``
(verified on this Mac, e.g. ``/private/tmp`` -> ``-private-tmp``, ``.hermes`` -> ``--hermes``).
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..text import ID_RE, SENSITIVE_TEXT_RE, TERMINAL
from .base import APPROVAL_CHOICES, BackendError, Capabilities, EventCallback

DEFAULT_BIN = "~/.local/bin/claude"
MAX_LINE = 8 * 1024 * 1024              # one stdout JSON line
MAX_EVENTS = 2000                        # per run; beyond this only important events are kept
MAX_EVENT_BYTES = 4 * 1024 * 1024        # per run, rough JSON size of the buffered log
MAX_TEXT = 8000                          # interim text
MAX_OUTPUT = 256 * 1024                  # final answer text
MAX_DIFF = 64 * 1024
MAX_STDERR = 16 * 1024
MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024
STOP_GRACE_S = 5.0
EXIT_GRACE_S = 10.0
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

TOOL_NAMES = {
    "Bash": "terminal", "BashOutput": "terminal", "Read": "read_file", "Grep": "search_files",
    "Glob": "search_files", "LS": "search_files", "Edit": "edit_file", "Write": "edit_file",
    "MultiEdit": "edit_file", "NotebookEdit": "edit_file", "WebFetch": "web_search",
    "WebSearch": "web_search",
}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
IMPORTANT = {"run.started", "approval.request", "files.changed"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/gif": ".gif",
               "image/webp": ".webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024       # one decoded image
MAX_MEDIA = 200                          # images written per run
DENY_MESSAGE = "The user denied this action."


def _one_line(value: Any, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\s+", " ", value).strip()
    if SENSITIVE_TEXT_RE.search(text):
        return "[redacted]"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def tool_short_name(name: Any) -> str:
    if not isinstance(name, str) or not name:
        return "tool"
    return TOOL_NAMES.get(name) or re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:64] or "tool"


@dataclass
class _Run:
    run_id: str
    idem_key: str
    session_id: str
    workspace: str
    proc: subprocess.Popen | None = None
    status: str = "running"
    output: str | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    event_bytes: int = 0
    dropped: int = 0
    cond: threading.Condition = field(default_factory=threading.Condition)
    stdin_lock: threading.Lock = field(default_factory=threading.Lock)
    stdin_open: bool = True
    started: bool = False
    stop_requested: bool = False
    steers_since_result: int = 0
    last_result: dict[str, Any] | None = None
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)  # ours -> {cli_id, input}
    cli_to_ours: dict[str, str] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)                  # tool_use_id -> CLI name
    held_text: str = ""
    media_count: int = 0
    viewed: set[str] = field(default_factory=set)                        # Read tool_use ids already shown
    stderr: bytearray = field(default_factory=bytearray)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL


class ClaudeCodeBackend:
    """TaskBackend over the ``claude`` CLI. One backend instance serves one workspace."""

    def __init__(self, workspace: str | os.PathLike[str], claude_bin: str | os.PathLike[str] | None = None,
                 model: str | None = None, permission_mode: str = "default",
                 extra_args: list[str] | None = None, env: dict[str, str] | None = None,
                 projects_dir: str | os.PathLike[str] | None = None,
                 stop_grace_s: float = STOP_GRACE_S, exit_grace_s: float = EXIT_GRACE_S,
                 media_root: str | os.PathLike[str] | None = None):
        self.workspace = str(Path(workspace).expanduser())
        self.claude_bin = str(Path(claude_bin or DEFAULT_BIN).expanduser())
        self.model = model or None
        self.permission_mode = permission_mode or "default"
        self.extra_args = list(extra_args or [])
        self.env = env
        self.projects_dir = Path(projects_dir).expanduser() if projects_dir else Path.home() / ".claude" / "projects"
        self.stop_grace_s, self.exit_grace_s = stop_grace_s, exit_grace_s
        self.media_root = Path(media_root).expanduser() if media_root else Path(tempfile.gettempdir()) / "speakeasy-media"
        self.last_error = ""
        self._lock = threading.Lock()
        self._runs: dict[str, _Run] = {}
        self._by_idem: dict[str, str] = {}

    # ---- contract: introspection -------------------------------------------------------------

    def capabilities(self) -> Capabilities:
        return Capabilities(kind="claude_code", display_name="Claude Code", steer=True, approvals=True,
                            file_changes=True, chat_delivery=False, threads=False,
                            conversation_continuity=False, email_drafts=False, daily_brief=False,
                            needs_workspace=True)

    def health(self, timeout: float = 2) -> bool:
        exe = self._resolve_bin()
        return bool(exe) and os.path.isdir(self.workspace)

    def _resolve_bin(self) -> str | None:
        if os.path.isfile(self.claude_bin) and os.access(self.claude_bin, os.X_OK):
            return self.claude_bin
        return shutil.which(self.claude_bin)

    # ---- contract: lifecycle -----------------------------------------------------------------

    def start_run(self, prompt: str, idem_key: str, session_id: str | None = None,
                  session_key: str | None = None) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise BackendError(400, "prompt is required")
        if not isinstance(idem_key, str) or not idem_key:
            raise BackendError(400, "idempotency key is required")
        if session_id is not None and (not isinstance(session_id, str) or not UUID_RE.fullmatch(session_id)):
            raise BackendError(400, "Claude Code session id must be a UUID")
        if not os.path.isdir(self.workspace):
            raise BackendError(400, "workspace folder does not exist")
        with self._lock:
            existing = self._by_idem.get(idem_key)
            if existing:
                return existing
            exe = self._resolve_bin()
            if not exe:
                raise BackendError(502, "Claude Code CLI not found")
            run = _Run(run_id="cc_" + secrets.token_hex(12), idem_key=idem_key,
                       session_id=session_id or str(uuid.uuid4()), workspace=self.workspace)
            args = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                    "--verbose", "--permission-prompt-tool", "stdio"]
            if self.permission_mode != "default":  # "default" is the CLI default and not in its help choices
                args += ["--permission-mode", self.permission_mode]
            if self.model:
                args += ["--model", self.model]
            args += ["--resume", session_id] if session_id else ["--session-id", run.session_id]
            args += self.extra_args
            try:
                run.proc = subprocess.Popen(args, cwd=self.workspace, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            env=self._env(), start_new_session=True)
            except OSError as exc:
                raise BackendError(502, f"could not start Claude Code: {type(exc).__name__}") from None
            self._runs[run.run_id] = run
            self._by_idem[idem_key] = run.run_id
        threading.Thread(target=self._read_stderr, args=(run,), daemon=True, name=f"cc-err-{run.run_id}").start()
        threading.Thread(target=self._read_stdout, args=(run,), daemon=True, name=f"cc-out-{run.run_id}").start()
        # If the process already died, the stdout reader reports run.failed.
        self._write(run, self._user_message(run, prompt))
        return run.run_id

    def events(self, run_id: str, callback: EventCallback) -> bool:
        run = self._get(run_id)
        index = 0
        while True:
            with run.cond:
                while index >= len(run.events) and not run.terminal:
                    run.cond.wait(timeout=1.0)
                batch = run.events[index:]
                index = len(run.events)
                done = run.terminal and index >= len(run.events)
            terminal = False
            for event in batch:
                callback(dict(event))
                if (event.get("event") or "").startswith("run.") and event["event"][4:] in TERMINAL:
                    terminal = True
            if terminal:
                return True
            if done:
                return False

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._get(run_id)
        with run.cond:
            result: dict[str, Any] = {"run_id": run.run_id, "status": run.status, "session_id": run.session_id}
            if run.output is not None:
                result["output"] = run.output
            if run.error is not None:
                result["error"] = run.error
        return result

    def stop(self, run_id: str) -> dict[str, Any]:
        run = self._get(run_id)
        with run.cond:
            if run.terminal:
                return self.get_run(run_id)
            run.stop_requested = True
        self._write(run, {"type": "control_request", "request_id": "stop_" + secrets.token_hex(6),
                          "request": {"subtype": "interrupt"}})
        # An interrupted turn still ends with a `result`; stdin is closed on that result so the CLI
        # exits. If it does not settle in time, end the process.
        with run.cond:
            run.cond.wait_for(lambda: run.terminal, timeout=self.stop_grace_s)
        if not run.terminal:
            self._close_stdin(run)
            self._kill(run)
            with run.cond:
                run.cond.wait_for(lambda: run.terminal, timeout=self.stop_grace_s)
        if not run.terminal:
            self._finish(run, "cancelled", error="stopped")
        return self.get_run(run_id)

    def steer(self, run_id: str, text: str) -> bool:
        try:
            run = self._get(run_id)
        except BackendError:
            return False
        if not isinstance(text, str) or not text.strip():
            return False
        with run.cond:
            if run.terminal or run.stop_requested or not run.stdin_open:
                return False
            run.steers_since_result += 1
        if self._write(run, self._user_message(run, text)):
            return True
        with run.cond:
            run.steers_since_result = max(0, run.steers_since_result - 1)
        return False

    def approve(self, run_id: str, request_id: str, choice: str) -> dict[str, Any]:
        if choice not in APPROVAL_CHOICES:
            raise BackendError(400, "approval choice must be once or deny")
        run = self._get(run_id)
        if not isinstance(request_id, str) or not ID_RE.fullmatch(request_id):
            raise BackendError(400, "invalid request ID")
        with run.cond:
            pending = run.approvals.get(request_id)
            if run.terminal or pending is None:
                raise BackendError(409, "no pending approval with that ID")
        if choice == "once":
            decision: dict[str, Any] = {"behavior": "allow", "updatedInput": pending["input"]}
        else:
            decision = {"behavior": "deny", "message": DENY_MESSAGE}
        if pending.get("tool_use_id"):
            decision["toolUseID"] = pending["tool_use_id"]
        sent = self._write(run, {"type": "control_response", "response": {
            "subtype": "success", "request_id": pending["cli_id"], "response": decision}})
        if not sent:
            raise BackendError(409, "Claude Code is no longer running")
        with run.cond:
            run.approvals.pop(request_id, None)
            run.cli_to_ours.pop(pending["cli_id"], None)
            if not run.approvals and run.status == "waiting_for_approval":
                run.status = "running"
        return {"run_id": run_id, "request_id": request_id, "choice": choice, "status": run.status}

    def session_messages(self, session_id: str, limit: int = 60, timeout: float = 1.5) -> list[dict[str, Any]]:
        if not isinstance(session_id, str) or not UUID_RE.fullmatch(session_id):
            return []
        path = self._transcript_path(session_id)
        if path is None:
            return []
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                if size > MAX_TRANSCRIPT_BYTES:
                    fh.seek(size - MAX_TRANSCRIPT_BYTES)
                    fh.readline()
                raw = fh.read()
        except OSError:
            return []
        messages: list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") not in {"user", "assistant"}:
                continue
            if entry.get("isSidechain") or entry.get("isMeta"):
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            text = _content_text(message.get("content"))
            if text:
                messages.append({"role": message.get("role") or entry["type"], "content": text})
        return messages[-limit:] if limit and limit > 0 else messages

    def run_to_completion(self, prompt: str, idem_key: str, session_id: str | None = None) -> tuple[str, str]:
        run_id = self.start_run(prompt, idem_key, session_id)
        self.events(run_id, lambda _event: None)
        result = self.get_run(run_id)
        status = result.get("status") or "unknown"
        if status != "completed" and isinstance(result.get("error"), str):
            self.last_error = result["error"]
        return status, result.get("output") or ""

    # ---- process I/O -------------------------------------------------------------------------

    def _env(self) -> dict[str, str]:
        env = dict(os.environ if self.env is None else self.env)
        env.setdefault("CLAUDE_CODE_ENTRYPOINT", "sdk-py")
        return env

    def _get(self, run_id: str) -> _Run:
        if not isinstance(run_id, str) or not ID_RE.fullmatch(run_id):
            raise BackendError(400, "invalid run ID")
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise BackendError(404, "unknown run")
        return run

    @staticmethod
    def _user_message(run: _Run, text: str) -> dict[str, Any]:
        return {"type": "user", "message": {"role": "user", "content": text},
                "parent_tool_use_id": None, "session_id": run.session_id}

    def _write(self, run: _Run, obj: dict[str, Any]) -> bool:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode()
        with run.stdin_lock:
            if not run.stdin_open or run.proc is None or run.proc.stdin is None:
                return False
            try:
                run.proc.stdin.write(data)
                run.proc.stdin.flush()
                return True
            except (OSError, ValueError):
                run.stdin_open = False
                return False

    def _close_stdin(self, run: _Run) -> None:
        with run.stdin_lock:
            if run.stdin_open and run.proc is not None and run.proc.stdin is not None:
                try:
                    run.proc.stdin.close()
                except OSError:
                    pass
            run.stdin_open = False

    def _kill(self, run: _Run) -> None:
        proc = run.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        except OSError:
            pass

    def _reap_later(self, run: _Run) -> None:
        def reap() -> None:
            proc = run.proc
            if proc is None:
                return
            try:
                proc.wait(timeout=self.exit_grace_s)
            except subprocess.TimeoutExpired:
                self._kill(run)
        threading.Thread(target=reap, daemon=True, name=f"cc-reap-{run.run_id}").start()

    def _read_stderr(self, run: _Run) -> None:
        stream = run.proc.stderr if run.proc else None
        if stream is None:
            return
        for chunk in iter(lambda: stream.read1(4096), b""):
            run.stderr += chunk
            if len(run.stderr) > MAX_STDERR:
                del run.stderr[: len(run.stderr) - MAX_STDERR]

    def _read_stdout(self, run: _Run) -> None:
        stream = run.proc.stdout if run.proc else None
        try:
            while stream is not None:
                line = stream.readline(MAX_LINE)
                if not line:
                    break
                if not line.endswith(b"\n") and len(line) >= MAX_LINE:
                    # Oversized line: drop the rest of it.
                    while True:
                        more = stream.readline(MAX_LINE)
                        if not more or more.endswith(b"\n"):
                            break
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    try:
                        self._handle(run, msg)
                    except Exception as exc:  # never let one odd line kill the reader
                        self.last_error = f"Claude Code event handling error: {type(exc).__name__}"
        finally:
            self._on_exit(run)

    def _on_exit(self, run: _Run) -> None:
        proc = run.proc
        code = None
        if proc is not None:
            try:
                code = proc.wait(timeout=self.exit_grace_s)
            except subprocess.TimeoutExpired:
                self._kill(run)
                code = proc.poll()
        self._close_stdin(run)
        if run.terminal:
            return
        if run.stop_requested:
            self._finish(run, "cancelled", error="stopped")
        elif run.last_result is not None:
            self._finish_from_result(run, run.last_result)
        else:
            detail = _one_line(run.stderr.decode(errors="replace")[-400:], 300)
            error = f"Claude Code exited (code {code}) without a result" + (f": {detail}" if detail else "")
            self._finish(run, "failed", error=error)

    # ---- translation -------------------------------------------------------------------------

    def _emit(self, run: _Run, event: dict[str, Any]) -> None:
        size = len(json.dumps(event, default=str))
        with run.cond:
            kind = event.get("event", "")
            important = kind in IMPORTANT or kind.startswith("run.")
            if not important and (len(run.events) >= MAX_EVENTS or run.event_bytes + size > MAX_EVENT_BYTES):
                run.dropped += 1
                return
            run.events.append(event)
            run.event_bytes += size
            run.cond.notify_all()

    def _finish(self, run: _Run, status: str, output: str | None = None, error: str | None = None) -> None:
        with run.cond:
            if run.terminal:
                return
            run.status, run.output, run.error = status, output, error
            run.approvals.clear()
            run.cli_to_ours.clear()
        event: dict[str, Any] = {"event": f"run.{status}", "run_id": run.run_id}
        if output is not None:
            event["output"] = output
        if error is not None:
            event["error"] = error
        # Status is already terminal; append the event directly so it is never capped.
        with run.cond:
            run.events.append(event)
            run.cond.notify_all()
        if status != "completed" and error:
            self.last_error = error
        self._close_stdin(run)
        self._reap_later(run)

    def _finish_from_result(self, run: _Run, msg: dict[str, Any]) -> None:
        text = msg.get("result") if isinstance(msg.get("result"), str) else ""
        if run.stop_requested:
            self._finish(run, "cancelled", output=text[:MAX_OUTPUT] or None, error="stopped")
        elif msg.get("is_error") or (msg.get("subtype") and msg.get("subtype") != "success"):
            errors = msg.get("errors")
            detail = text or ("; ".join(e for e in errors if isinstance(e, str)) if isinstance(errors, list) else "")
            self._finish(run, "failed", error=_one_line(detail or f"Claude Code {msg.get('subtype') or 'error'}", 500))
        else:
            self._finish(run, "completed", output=text[:MAX_OUTPUT])

    def _handle(self, run: _Run, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "system":
            if msg.get("subtype") == "init":
                sid = msg.get("session_id")
                with run.cond:
                    if isinstance(sid, str) and UUID_RE.fullmatch(sid):
                        run.session_id = sid
                    first = not run.started
                    run.started = True
                if first:
                    self._emit(run, {"event": "run.started", "run_id": run.run_id, "session_id": run.session_id})
        elif kind == "assistant":
            self._handle_assistant(run, msg)
        elif kind == "user":
            self._handle_user(run, msg)
        elif kind == "control_request":
            self._handle_control_request(run, msg)
        elif kind == "control_cancel_request":
            cli_id = msg.get("request_id")
            with run.cond:
                ours = run.cli_to_ours.pop(cli_id, None) if isinstance(cli_id, str) else None
                if ours:
                    run.approvals.pop(ours, None)
                if not run.approvals and run.status == "waiting_for_approval":
                    run.status = "running"
        elif kind == "result":
            with run.cond:
                run.last_result = msg
                run.held_text = ""
                steered = run.steers_since_result > 0
                run.steers_since_result = 0
            if steered and not run.stop_requested:
                # A steer was queued during this turn: let the CLI finish it, accept no more input.
                self._close_stdin(run)
                return
            self._finish_from_result(run, msg)

    def _handle_assistant(self, run: _Run, msg: dict[str, Any]) -> None:
        message = msg.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                # Text is interim commentary only if more tool use follows, so hold it until then
                # (the CLI emits one content block per assistant message). A turn's last text is
                # the answer and arrives again as the `result`.
                with run.cond:
                    run.held_text = (run.held_text + "\n" + block["text"]).strip()[-MAX_TEXT:]
            elif block.get("type") == "tool_use":
                self._flush_held_text(run)
                name = block.get("name")
                if isinstance(block.get("id"), str) and isinstance(name, str):
                    with run.cond:
                        if len(run.tools) < 10000:
                            run.tools[block["id"]] = name
                self._emit(run, {"event": "tool.started", "tool": tool_short_name(name),
                                 "preview": self._preview(name, block.get("input"))})
                if name == "Read":
                    self._maybe_viewed(run, block.get("id"), block.get("input"))

    def _flush_held_text(self, run: _Run) -> None:
        with run.cond:
            text, run.held_text = run.held_text, ""
        self._emit_interim(run, text)

    def _emit_interim(self, run: _Run, text: str) -> None:
        text = text.strip()
        if text:
            self._emit(run, {"event": "message.interim", "text": text[:MAX_TEXT]})

    def _handle_user(self, run: _Run, msg: dict[str, Any]) -> None:
        message = msg.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        edited = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id") if isinstance(block.get("tool_use_id"), str) else ""
                if run.tools.get(tool_use_id) in EDIT_TOOLS and not block.get("is_error"):
                    edited = True
                if tool_use_id not in run.viewed:  # a Read of an image file was already shown by path
                    self._images_from_result(run, run.tools.get(tool_use_id), block.get("content"))
        if edited:
            change = workspace_changes(run.workspace)
            if change is not None:
                self._emit(run, {"event": "files.changed", **change})

    def _maybe_viewed(self, run: _Run, tool_use_id: Any, tool_input: Any) -> None:
        """A Read of an image file: show that file (``source: viewed``)."""
        raw = tool_input.get("file_path") if isinstance(tool_input, dict) else None
        if not isinstance(raw, str) or Path(raw).suffix.lower() not in IMAGE_EXTS:
            return
        path = Path(raw) if os.path.isabs(raw) else Path(run.workspace) / raw
        path = Path(os.path.abspath(path))
        if not path.is_file():
            return  # the tool_result's inline image (if any) is shown instead
        if isinstance(tool_use_id, str):
            run.viewed.add(tool_use_id)
        self._emit(run, {"event": "media.seen", "path": str(path), "name": path.name, "source": "viewed"})

    def _images_from_result(self, run: _Run, tool: str | None, content: Any) -> None:
        """Inline base64 images in a tool_result (browser/Playwright screenshots, Read of an image
        outside the workspace): written to ``<media_root>/<run_id>/<n>.<ext>``."""
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            source = block.get("source") if isinstance(block.get("source"), dict) else {}
            if source.get("type") == "base64":
                data, media_type = source.get("data"), source.get("media_type")
            else:  # raw MCP image content: {type: image, data, mimeType}
                data, media_type = block.get("data"), block.get("mimeType")
            ext = IMAGE_TYPES.get(str(media_type).lower())
            if not isinstance(data, str) or not ext or len(data) > MAX_IMAGE_BYTES * 4 // 3 + 4:
                continue
            try:
                raw = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                continue
            with run.cond:
                if run.media_count >= MAX_MEDIA:
                    return
                run.media_count += 1
                n = run.media_count
            folder = self.media_root / run.run_id
            try:
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = folder / f"{n}{ext}"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw)
            except OSError:
                continue
            short = (tool or "").lower()
            kind = "viewed" if tool == "Read" else "generated" if "generat" in short else "screenshot"
            self._emit(run, {"event": "media.seen", "path": str(path.resolve()), "name": path.name,
                             "source": kind})

    def _handle_control_request(self, run: _Run, msg: dict[str, Any]) -> None:
        cli_id = msg.get("request_id")
        raw_request = msg.get("request")
        request: dict[str, Any] = raw_request if isinstance(raw_request, dict) else {}
        if not isinstance(cli_id, str):
            return
        if request.get("subtype") != "can_use_tool":
            self._write(run, {"type": "control_response", "response": {
                "subtype": "error", "request_id": cli_id,
                "error": f"unsupported control request: {request.get('subtype')}"}})
            return
        # Text right before a permission prompt is commentary too.
        self._flush_held_text(run)
        raw_tool, raw_input = request.get("tool_name"), request.get("input")
        tool = raw_tool if isinstance(raw_tool, str) else "tool"
        tool_input: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
        ours = "ap_" + secrets.token_hex(10)
        kind, description = self._describe(tool, tool_input, request.get("description"))
        with run.cond:
            if run.terminal:
                return
            run.approvals[ours] = {"cli_id": cli_id, "input": tool_input, "tool": tool,
                                   "tool_use_id": request.get("tool_use_id")
                                   if isinstance(request.get("tool_use_id"), str) else None}
            run.cli_to_ours[cli_id] = ours
            run.status = "waiting_for_approval"
        self._emit(run, {"event": "approval.request", "request_id": ours, "description": description,
                         "kind": kind})

    # ---- presentation ------------------------------------------------------------------------

    def _rel(self, path: Any) -> str:
        if not isinstance(path, str) or not path:
            return ""
        try:
            p = Path(path)
            if p.is_absolute():
                for root in {self.workspace, os.path.realpath(self.workspace)}:
                    try:
                        return str(p.relative_to(root))
                    except ValueError:
                        continue
                return p.name
        except (ValueError, OSError):
            pass
        return path

    def _preview(self, name: Any, tool_input: Any) -> str:
        data = tool_input if isinstance(tool_input, dict) else {}
        if name == "Bash":
            value = data.get("command")
            value = value.splitlines()[0] if isinstance(value, str) and value else value
        elif name in {"Read", "Edit", "Write", "MultiEdit"}:
            value = self._rel(data.get("file_path"))
        elif name == "NotebookEdit":
            value = self._rel(data.get("notebook_path"))
        elif name in {"Grep", "Glob"}:
            value = data.get("pattern")
        elif name == "WebFetch":
            value = data.get("url")
        elif name == "WebSearch":
            value = data.get("query")
        else:
            value = data.get("description") or data.get("prompt") or ""
        return _one_line(value, 160)

    def _describe(self, tool: str, data: dict[str, Any], cli_description: Any) -> tuple[str, str]:
        if tool == "Bash":
            cmd = _one_line(data.get("command"), 120) or "a command"
            where = self._rel(data.get("cwd")) if isinstance(data.get("cwd"), str) else ""
            folder = where or os.path.basename(os.path.realpath(self.workspace)) or "the workspace"
            return "command", f"Run `{cmd}` in {folder}"
        if tool in EDIT_TOOLS:
            path = self._rel(data.get("file_path") or data.get("notebook_path")) or "a file"
            return "file_change", f"Edit {_one_line(path, 160)}"
        detail = _one_line(cli_description, 200)
        if detail:
            return "permission", f"{tool}: {detail}"
        preview = self._preview(tool, data)
        return "permission", f"Use {tool}" + (f" ({preview})" if preview else "")

    def _transcript_path(self, session_id: str) -> Path | None:
        for cwd in {self.workspace, os.path.realpath(self.workspace)}:
            candidate = self.projects_dir / re.sub(r"[^A-Za-z0-9]", "-", cwd) / f"{session_id}.jsonl"
            if candidate.is_file():
                return candidate
        try:
            for candidate in self.projects_dir.glob(f"*/{session_id}.jsonl"):
                if candidate.is_file():
                    return candidate
        except OSError:
            pass
        return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [b["text"] for b in content
             if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    return "\n".join(parts).strip()


def workspace_changes(workspace: str, runner: Callable[..., Any] = subprocess.run,
                      max_untracked: int = 200) -> dict[str, Any] | None:
    """``{files: [{path, added, removed}], diff}``: the working tree vs HEAD, including new
    untracked files; None if the workspace is not a git repo. Read-only: never touches the index."""
    def git(*args: str, ok: tuple[int, ...] = (0,)) -> str | None:
        try:
            proc = runner(["git", "-C", workspace, "--no-pager", *args], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode not in ok:
            return None
        return proc.stdout.decode(errors="replace")

    inside = git("rev-parse", "--is-inside-work-tree")
    if not inside or inside.strip() != "true":
        return None
    base = ["HEAD"] if git("rev-parse", "--verify", "-q", "HEAD") is not None else ["--cached"]
    numstat = git("diff", "--numstat", *base) or ""
    diff = git("diff", *base) or ""
    untracked = [p for p in (git("ls-files", "--others", "--exclude-standard", "-z") or "").split("\0") if p]
    for path in untracked[:max_untracked]:
        # `git diff --no-index` exits 1 when the files differ, which is the normal case here.
        numstat += git("diff", "--no-index", "--numstat", "--", os.devnull, path, ok=(0, 1)) or ""
        if len(diff) <= MAX_DIFF:
            diff += git("diff", "--no-index", "--", os.devnull, path, ok=(0, 1)) or ""
    files = []
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        if " => " in path and path.startswith(os.devnull):
            path = path.split(" => ", 1)[1]
        files.append({"path": path, "added": int(added) if added.isdigit() else 0,
                      "removed": int(removed) if removed.isdigit() else 0})
    if len(diff.encode()) > MAX_DIFF:
        diff = diff.encode()[:MAX_DIFF].decode(errors="ignore") + "\n… diff truncated …\n"
    return {"files": files, "diff": diff}
