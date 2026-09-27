"""One voice call: its live state, the SSE event feed, chat notices, and the sideband worker that
turns the voice model's handoffs into Hermes tasks and speaks their results back.

Provider-neutral: ``openai_live.OpenAISidebandWorker`` and ``codex_transport.CodexSidebandWorker``
only implement ``run()`` (read provider events) and ``_send()`` (write context to the call).
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import queue
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from . import channels, continuity, router
from .prompt import builder as P
from .text import (ID_RE, MAX_TRANSCRIPT, TERMINAL, clean_transcript, delivery_text, derive_tool_status,
                   interim_progress, notice_text, safe_user_text, short_title, split_result)

logger = logging.getLogger(__name__)

TASK_SESSION_PREFIX = "speakeasy_task_"
MAX_TASKS = 8
ACTIVE_RUN_STATES = {"admitting", "running", "working", "waiting_for_approval", "resolving_approval",
                     "cancel_requested"}
CONTINUITY_WAIT_S = 600
THREAD_OPEN_WAIT_S = 20
CONTINUITY_POLL_S = 5
# Spoken progress: only for tasks running this long, at most once per task per PROGRESS_EVERY_S,
# and only when the user hasn't spoken since the last update.
PROGRESS_AFTER_S = 20.0
PROGRESS_EVERY_S = 30.0


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclasses.dataclass
class Runtime:
    """Everything a worker needs besides the call itself (built once by the server)."""
    store: Any
    hermes: Any
    settings: Callable[[], dict[str, Any]]
    hermes_home: Path
    image_roots: Callable[[], tuple[Path, ...]]
    notices: "Notices"
    hermes_key: Callable[[], str] = lambda: ""
    # Routing: the model call (None = Hermes' auxiliary client, task speakeasy_router) and the
    # new-thread runner (None = channels only get results posted, never a thread).
    route_call: Callable[[list[dict[str, str]]], str | None] | None = None
    threads: Any = None

    @property
    def names(self) -> P.Names:
        return P.Names.from_settings(self.settings())

    @property
    def state_db(self) -> Path:
        return self.hermes_home / "state.db"

    def delivery_label(self) -> str:
        from .delivery import target_label
        return target_label(self.settings()["delivery"]["target"])

    def channel_label(self, target: str) -> str:
        found = next((c for c in channels.opted_in(self.settings()) if c.target == target), None)
        return found.label if found else self.delivery_label()

    def route(self, request: str, tasks: list[router.OpenTask], marked: Any) -> router.Decision:
        topics = [router.Topic(c.label, c.topic) for c in channels.opted_in(self.settings())]
        return router.decide(request, tasks, marked, topics, self.route_call)

    def explicit_channel(self, request: str) -> channels.Choice | None:
        return channels.explicit(request, self.settings(), P.clarify_channel)

    def topical_channel(self, picked: str | None) -> channels.Choice:
        return channels.resolve(self.settings(), self.delivery_label(), picked)


# -- live event feed ------------------------------------------------------------------------

class EventFeed:
    """Bounded per-interaction event ring feeding the live SSE stream."""
    RING = 200

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.publish_lock = threading.Lock()
        self.ring: deque[tuple[int, str, Any]] = deque(maxlen=self.RING)
        self.seq = 0
        self.last: dict[str, Any] = {"interaction": None, "work": None, "approval": None, "tasks": None,
                                     "email_drafts": None}
        self.closed_id: int | None = None

    def publish(self, kind: str, payload: Any) -> None:
        with self.cond:
            if kind in self.last and self.last[kind] == payload:
                return  # dedupe identical consecutive payloads per type
            if kind == "closed" and self.closed_id is not None:
                return
            self.seq += 1
            self.last[kind] = payload
            self.ring.append((self.seq, kind, payload))
            if kind == "closed":
                self.closed_id = self.seq
            self.cond.notify_all()

    def since(self, last_id: int) -> list[tuple[int, str, Any]] | None:
        """Events after last_id, or None when the gap is outside the retained window."""
        if last_id == self.seq:
            return []
        if last_id > self.seq or not self.ring or self.ring[0][0] > last_id + 1:
            return None
        return [event for event in self.ring if event[0] > last_id]


# -- call state -------------------------------------------------------------------------------

@dataclasses.dataclass
class BackendRun:
    delegation_id: str
    revision: int
    idem_key: str
    run_id: str | None = None
    status: str = "admitting"
    approval: dict[str, Any] | None = None
    error: str | None = None
    # A part split from one spoken request, or a follow-up run: the voice delegation it answers.
    voice_id: str | None = None
    # Where the finished answer is posted when it is not the default target (a routed channel).
    deliver_to: str | None = None
    started: float = dataclasses.field(default_factory=time.monotonic)
    spoken_at: float = 0.0          # last spoken progress line for this task (monotonic)

    @property
    def say_id(self) -> str:
        return self.voice_id or self.delegation_id


@dataclasses.dataclass
class Interaction:
    interaction_id: str
    live_session_id: str
    status: str = "connecting"
    revision: int = 0
    finalization: str = "open"
    error: str | None = None
    runs: dict[str, BackendRun] = dataclasses.field(default_factory=dict)
    latest_delegation_id: str | None = None
    away: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    call_closed: bool = False
    paused: bool = False
    pause_bookkept: bool = False
    history: list[dict[str, str]] = dataclasses.field(default_factory=list)
    resumed_from: str | None = None
    device_id: str = ""
    last_activity: float = dataclasses.field(default_factory=time.monotonic)
    successor: "Interaction | None" = dataclasses.field(default=None, repr=False)
    worker: Any = dataclasses.field(default=None, repr=False)
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock, repr=False)
    feed: EventFeed = dataclasses.field(default_factory=EventFeed, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            latest = self.runs.get(self.latest_delegation_id or "")
            approval = None
            pending = next((run for run in reversed(list(self.runs.values())) if run.approval), None)
            if pending and pending.approval:
                approval = dict(pending.approval)
                approval["run_id"] = pending.run_id
                approval["delegation_id"] = pending.delegation_id
            return {
                "interaction_id": self.interaction_id,
                "status": latest.status if latest else self.status,
                "run_id": latest.run_id if latest else None,
                "backend_run_id": latest.run_id if latest else None,
                "delegation_id": latest.delegation_id if latest else None,
                "revision": self.revision,
                "approval": approval,
                "finalization": self.finalization,
                "error": (latest.error if latest else None) or self.error,
                "paused": self.paused,
                "resumed_from": self.resumed_from,
            }

    def head(self) -> "Interaction":
        """The newest call in this Pause/Resume chain (results are routed there)."""
        current = self
        while current.successor is not None:
            current = current.successor
        return current


def interaction_tasks(store: Any, interaction: Interaction, assistant_name: str = "Hermes") -> list[dict[str, Any]]:
    """Every voice task of this call (parallel runs, oldest first), as work objects + task_id."""
    with interaction.lock:
        runs = [(r.delegation_id, r.idem_key, r.run_id, r.status) for r in interaction.runs.values()]
    tasks = []
    for delegation_id, key, run_id, status in runs[-MAX_TASKS:]:
        if status == "rejected":
            continue
        work = store.work(idem_key=key, assistant_name=assistant_name)
        if work is not None and work.get("dismissed"):
            continue
        if work is None:
            work = {"run_id": run_id, "source_run_id": run_id, "status": status, "stale": False,
                    "updated": None, "events": [], "short_status": None, "detail": None,
                    "updated_at": None, "status_source": None, "result": None, "email_drafts": []}
        work["task_id"] = delegation_id
        tasks.append(work)
    return tasks


def publish_state(store: Any, interaction: Interaction, assistant_name: str = "Hermes") -> None:
    """Recompute interaction/work/approval/tasks/drafts and push changes to the live event feed.

    Never call while holding interaction.lock. Unchanged payloads are deduplicated.
    """
    feed = interaction.feed
    with feed.publish_lock:
        if feed.closed_id is not None:
            return
        snap = interaction.snapshot()
        with interaction.lock:
            active = any(run.status in ACTIVE_RUN_STATES for run in interaction.runs.values())
        work = store.work(snap["run_id"], assistant_name=assistant_name) if snap["run_id"] else None
        pending = snap.get("approval")
        approval = None
        if pending:
            approval = {"run_id": pending.get("run_id"), "request_id": pending.get("request_id"),
                        "description": pending.get("description"), "choices": ["once", "deny"]}
        tasks = interaction_tasks(store, interaction, assistant_name)
        drafts = [dict(d, task_id=t["task_id"], run_id=t.get("run_id")) for t in tasks for d in t.get("email_drafts") or []]
        feed.publish("interaction", snap)
        feed.publish("work", work)
        feed.publish("tasks", tasks)
        feed.publish("approval", approval)
        feed.publish("email_drafts", drafts)
        if snap["finalization"] in {"confirmed", "incomplete"} and not active:
            feed.publish("closed", {
                "finalization": "complete" if snap["finalization"] == "confirmed" else "incomplete",
                "error": snap.get("error"),
            })


# -- chat notices (delivery target) ---------------------------------------------------------------

class Notices:
    """Deduplicated, fire-and-forget chat notices to the delivery target, from one background thread.

    `post` only claims a SQLite dedupe key and enqueues; it never blocks on delivery and never raises
    into call handling. With delivery.target = none nothing is posted.
    """

    def __init__(self, store: Any, notifier: Any | None, target: Callable[[], str], names: Callable[[], P.Names]):
        self.store, self.notifier, self.target, self.names = store, notifier, target, names
        self.queue: "queue.Queue[tuple[str, str, str]]" = queue.Queue()
        if notifier is not None:
            threading.Thread(target=self._worker, daemon=True, name="speakeasy-notices").start()

    def post(self, dedupe_key: str, text: str, limit: int = 600, target: str | None = None) -> bool:
        target = target or self.target()
        if self.notifier is None or not target or target == "none" or not text.strip():
            return False
        try:
            if not self.store.claim_notice(dedupe_key):
                return False
            self.queue.put((dedupe_key, target, text[:limit]))
            return True
        except Exception as exc:  # never break the voice path
            logger.warning("speakeasy: notice enqueue failed: %s", type(exc).__name__)
            return False

    def _worker(self) -> None:
        while True:
            key, target, text = self.queue.get()
            try:
                outcome = "sent" if self.notifier.send(target, text) else "failed"
            except Exception as exc:
                outcome = f"failed: {type(exc).__name__}"
            try:
                self.store.notice_outcome(key, outcome)
            except Exception:
                pass
            self.queue.task_done()

    def still_working(self, run_id: str, request: str | None, short_status: str | None) -> None:
        self.post(f"still:{run_id}", P.still_working_notice(request, short_status))

    def needs_you(self, request_id: str, summary: str | None) -> None:
        self.post(f"approval:{request_id}", P.needs_you_notice(self.names(), summary))

    def answered(self, run_id: str, full: str | None, target: str | None = None) -> None:
        """Post a finished answer in full, once per run (`hermes send` splits long text), to the
        task's routed channel when it has one, else the default target."""
        text = (full or "").strip()
        if text:
            self.post(f"answer:{run_id}", text, limit=20000, target=target)

    def stopped(self, run_id: str, status: str, request: str | None) -> None:
        self.post(f"stopped:{run_id}", P.stopped_notice(self.names(), status, request))

    def draft_waiting(self, draft_id: str, subject: str | None) -> None:
        self.post(f"draft:{draft_id}", P.draft_notice(subject))


# -- the sideband worker ------------------------------------------------------------------------

class SidebandWorker:
    """Base worker. Subclasses read provider events in ``run()`` and write in ``_send()``."""

    def __init__(self, rt: Runtime, interaction: Interaction):
        self.rt, self.store, self.hermes, self.interaction = rt, rt.store, rt.hermes, interaction
        self.notices = rt.notices
        self.fragments: deque[dict[str, Any]] = deque(maxlen=512)
        self.handoff_at: dict[str, float] = {}   # delegation id -> when the handoff arrived (monotonic)
        self.route_timings: dict[str, int] = {}  # task id -> routing latency (ms), stored with the task
        self.delegations: set[str] = set()
        self.dispatch_tasks: set[asyncio.Task[Any]] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.connected = False
        interaction.worker = self

    # -- plumbing -------------------------------------------------------------------------
    @property
    def names(self) -> P.Names:
        return self.rt.names

    def publish(self) -> None:
        name = self.names.assistant_name
        publish_state(self.store, self.interaction, name)
        head = self.interaction.head()
        if head is not self.interaction:
            publish_state(self.store, head, name)

    async def _publish_async(self) -> None:
        self.publish()

    def start(self) -> None:
        threading.Thread(target=lambda: asyncio.run(self.run()), daemon=True,
                         name=f"speakeasy-call-{self.interaction.interaction_id[:12]}").start()

    async def run(self) -> None:  # pragma: no cover - provider-specific
        raise NotImplementedError

    async def _send(self, kind: str, delegation_id: str | None, content: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def stop_transport(self) -> None:
        """Close the provider session (idle pause, end). Provider-specific; default no-op."""

    def call_connected(self) -> bool:
        return self.connected and not self.interaction.call_closed

    def turns(self) -> list[dict[str, str]]:
        """This call's whole transcript as cleaned speaker turns."""
        out: list[dict[str, str]] = []
        for fragment in self.fragments:
            role = fragment["speaker"]
            if out and out[-1]["role"] == role:
                out[-1]["text"] += fragment["text"]
            else:
                out.append({"role": role, "text": fragment["text"]})
        return [{"role": t["role"], "text": clean_transcript(t["text"]).strip()}
                for t in out if clean_transcript(t["text"]).strip()]

    def call_closed(self) -> None:
        """Once per call: persist the end time, keep recent turns, notify about work in flight.

        A paused call keeps its transcript for Resume and defers the "Still working" notices.
        """
        with self.interaction.lock:
            if self.interaction.call_closed:
                return
            self.interaction.call_closed = True
            self.interaction.history = (self.interaction.history + self.turns())[-400:]
            paused = self.interaction.paused
            history = list(self.interaction.history)
        try:
            if history:
                self.store.set_meta("recent_voice", json.dumps(history[-30:]))
            if not paused:
                self.store.mark_call_ended()
                self.still_working_notices()
        except Exception as exc:
            logger.warning("speakeasy: call-close bookkeeping failed: %s", type(exc).__name__)

    def still_working_notices(self) -> None:
        with self.interaction.lock:
            runs = [(r.run_id, r.idem_key, r.status, dict(r.approval) if r.approval else None)
                    for r in self.interaction.runs.values()]
        try:
            for run_id, key, status, approval in runs:
                if not run_id or status not in ACTIVE_RUN_STATES:
                    continue
                if approval:
                    self.notices.needs_you(approval["request_id"], approval.get("description"))
                    continue
                work = self.store.work(run_id) or {}
                short = work.get("short_status") if work.get("status_source") != "system" else None
                self.notices.still_working(run_id, self.store.request_text(key), short)
        except Exception as exc:
            logger.warning("speakeasy: still-working notices failed: %s", type(exc).__name__)

    def touch(self) -> None:
        self.interaction.last_activity = time.monotonic()

    # -- provider events --------------------------------------------------------------------
    async def handle_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind in {"session.input_transcript.delta", "session.output_transcript.delta"}:
            delta = event.get("delta")
            if not isinstance(delta, str) or not delta or len(delta.encode()) > 8192:
                return
            self.touch()
            speaker = "user" if kind.startswith("session.input") else "assistant"
            self.fragments.append({"speaker": speaker, "text": delta, "at": time.monotonic(),
                                   "start_ms": int(event.get("start_ms", 0)), "end_ms": int(event.get("end_ms", 0))})
            return
        if kind == "session.delegation.created":
            delegation = event.get("delegation") or {}
            delegation_id = delegation.get("id")
            if delegation.get("target") != "client" or not isinstance(delegation_id, str) or not ID_RE.fullmatch(delegation_id):
                return
            if delegation_id in self.delegations:
                return
            self.delegations.add(delegation_id)
            self.touch()
            # delegation.created is the documented utterance boundary: freeze the transcript now.
            context = self.context(int(event.get("offset_ms", 0)))
            marked = delegation.get("task_id") or delegation.get("follow_up_task_id")
            self.schedule_dispatch(delegation_id, context, marked)
        elif kind == "session.closed":
            self.call_closed()
            with self.interaction.lock:
                self.interaction.finalization = "confirmed"
                latest = self.interaction.runs.get(self.interaction.latest_delegation_id or "")
                if not latest or latest.status not in {"working", "running", "waiting_for_approval"}:
                    self.interaction.status = "closed"
            self.publish()

    def schedule_dispatch(self, delegation_id: str, context: str, marked: Any = None) -> None:
        self.handoff_at[delegation_id] = time.monotonic()
        with self.interaction.lock:
            self.interaction.revision += 1
            revision = self.interaction.revision
        task = asyncio.get_running_loop().create_task(self.dispatch(delegation_id, revision, context, marked))
        self.dispatch_tasks.add(task)
        task.add_done_callback(self.dispatch_tasks.discard)

    def context(self, offset_ms: int) -> str:
        selected = [f for f in self.fragments if (f["end_ms"] or f["start_ms"]) <= offset_ms]
        lines: list[str] = []
        current = None
        for fragment in selected:
            if fragment["speaker"] != current:
                current = fragment["speaker"]
                lines.append(f"\n{current.title()}: ")
            lines[-1] += fragment["text"]
        cleaned: list[str] = []
        for line in lines:
            label, _, body = line.partition(": ")
            body = clean_transcript(body)
            if body:
                cleaned.append(f"{label}: {body}")
        text = "".join(cleaned).strip()
        earlier = self.interaction.history if self.interaction.resumed_from else []
        if earlier and text:
            before = "\n".join(f"{t['role'].title()}: {t['text']}" for t in earlier[-40:])
            text = f"Earlier in this call, before it was paused:\n{before}\n\nAfter resuming:\n{text}"
        return text[-MAX_TRANSCRIPT:]

    async def append(self, kind: str, delegation_id: str | None, content: str) -> None:
        """Send context to the live call. After Pause/Resume, results of runs started in an earlier
        call go to the resumed call as session-wide context."""
        head = self.interaction.head()
        target = head.worker if head is not self.interaction else self
        if target is None or target is not self and not target.call_connected():
            return
        if target is not self:
            content = "(Update on a task from before the pause.) " + content
            await target.send_on_own_loop(kind, None, content)
            return
        await self._send(kind, delegation_id, content[:2000])

    async def send_on_own_loop(self, kind: str, delegation_id: str | None, content: str) -> None:
        loop, current = self.loop, asyncio.get_running_loop()
        if loop is None or loop is current:
            await self._send(kind, delegation_id, content[:2000])
            return
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self._send(kind, delegation_id, content[:2000]), loop))

    def speak_from_thread(self, kind: str, content: str) -> None:
        """Called from HTTP threads (e.g. an email card decision) to tell the live call something."""
        head = self.interaction.head()
        worker = head.worker or self
        loop = worker.loop
        if loop is None or not worker.call_connected():
            return
        asyncio.run_coroutine_threadsafe(worker._send(kind, None, content[:2000]), loop)

    # -- spoken acknowledgement and progress -------------------------------------------------
    def assistant_spoke_since(self, since: float) -> bool:
        return any(f["speaker"] == "assistant" and f.get("at", 0) >= since and clean_transcript(f["text"]).strip()
                   for f in list(self.fragments))

    def user_spoke_since(self, since: float) -> bool:
        return any(f["speaker"] == "user" and f.get("at", 0) >= since and clean_transcript(f["text"]).strip()
                   for f in list(self.fragments))

    async def say_where(self, delegation_id: str, line: str | None) -> None:
        """Speak where a task went (a new thread, another channel). The voice model acknowledges
        work on its own; this is the one thing only the server knows, so it is said as-is."""
        self.handoff_at.pop(delegation_id, None)
        if line:
            await self.append("session.commentary.append", delegation_id, line)

    def record_timing(self, task_id: str, idem: str) -> None:
        ms = self.route_timings.pop(task_id, None)
        if ms is not None:
            self.store.set_timing(idem, "routing_ms", ms)

    async def maybe_speak_progress(self, backend: BackendRun, milestone: str) -> None:
        """A long task with a new milestone gets one brief spoken update, at most once per 30 s, and
        only while the user is quiet (never talk over them or chain updates)."""
        if not self.rt.settings()["speech"]["progress"]:
            return
        now = time.monotonic()
        with self.interaction.lock:
            last = backend.spoken_at or backend.started
            if (now - backend.started < PROGRESS_AFTER_S or (backend.spoken_at and now - backend.spoken_at < PROGRESS_EVERY_S)
                    or backend.status not in {"running", "working"}):
                return
            if self.user_spoke_since(last):
                backend.spoken_at = now  # they talked meanwhile: restart the quiet window
                return
            backend.spoken_at = now
        if self.call_connected():
            await self.append("session.commentary.append", backend.say_id, P.progress_line(backend.idem_key, milestone))

    # -- dispatch ---------------------------------------------------------------------------
    def _idem(self, key: str, revision: int) -> str:
        return "se_" + hashlib.sha256(
            f"{self.interaction.interaction_id}\0{key}\0{revision}\0coordinator".encode()).hexdigest()

    def open_tasks(self) -> list[router.OpenTask]:
        with self.interaction.lock:
            runs = [(r.delegation_id, r.idem_key, r.status, r.started) for r in self.interaction.runs.values()
                    if r.status != "rejected"]
        tasks = []
        now = time.monotonic()
        for task_id, key, status, started in runs[-MAX_TASKS:]:
            request = self.store.request_text(key)
            if not request:
                continue
            work = self.store.work(idem_key=key) or {}
            spoken = ((work.get("result") or {}).get("spoken") or "") if status == "completed" else ""
            tasks.append(router.OpenTask(task_id, request, status, spoken, round(now - started, 1)))
        return tasks

    async def dispatch(self, delegation_id: str, revision: int, context: str, marked: Any = None) -> None:
        """One delegation from the live call: a new task (the usual case), a follow-up to an open
        task, or a request to continue one of the user's existing Hermes conversations."""
        if not context:
            backend = BackendRun(delegation_id, revision, self._idem(delegation_id, revision))
            with self.interaction.lock:
                self.interaction.runs[delegation_id] = backend
                self.interaction.latest_delegation_id = delegation_id
                backend.status = "rejected"
                backend.error = "No complete transcript was available at the delegation boundary"
            self.publish()
            await self.append("session.commentary.append", delegation_id, P.NO_TRANSCRIPT_SPOKEN)
            return
        last_request = next((line[6:].strip() for line in reversed(context.splitlines())
                             if line.startswith("User: ")), "")
        decision = await asyncio.to_thread(self.rt.route, last_request, self.open_tasks(), marked)
        self.route_timings[delegation_id] = decision.latency_ms
        part = decision.parts[0]
        if part.kind == "follow_up" and part.task_id:
            await self.follow_up(delegation_id, revision, context, part)
            return
        named = self.rt.explicit_channel(last_request)
        if named is None and len(decision.parts) == 1:
            conv = await self.match_conversation(last_request)
            if conv is not None:
                await self.start_continuity_task(delegation_id, revision, context, last_request, conv)
                return
        choice = named or self.rt.topical_channel(decision.channel)
        if len(decision.parts) > 1 and not choice.clarify:
            await self.start_parts(delegation_id, revision, context, [p.request for p in decision.parts], choice)
            return
        await self.start_routed(delegation_id, revision, context, last_request, choice)

    async def start_parts(self, delegation_id: str, revision: int, context: str, parts: list[str],
                          choice: channels.Choice) -> None:
        """A compound request: one parallel task per part, one spoken acknowledgement for all."""
        channel = choice.channel
        deliver_to = channel.target if channel is not None and not channel.default else None
        await self.append("session.thinking.append", delegation_id, P.split_note(self.names, parts))
        self.handoff_at.pop(delegation_id, None)
        self.route_timings.update({f"{delegation_id}-p{i}": self.route_timings.get(delegation_id, 0)
                                   for i in range(len(parts))})
        await asyncio.gather(*(self.start_task(f"{delegation_id}-p{i}", revision, context, part, focus=part,
                                               voice_id=delegation_id, deliver_to=deliver_to)
                               for i, part in enumerate(parts)))

    async def start_routed(self, delegation_id: str, revision: int, context: str, request: str,
                           choice: channels.Choice) -> None:
        """A new task, placed by channel routing: ask when unclear, open a thread where the
        channel wants one (and Hermes can), else run here and post the answer to the channel."""
        if choice.clarify:
            self.handoff_at.pop(delegation_id, None)
            await self.append("session.commentary.append", delegation_id, choice.clarify)
            return
        channel = choice.channel
        routed = channel is not None and not channel.default
        runner = self.rt.threads
        if channel is not None and channel.new_thread and runner is not None:
            available = await asyncio.to_thread(runner.available, channel.target)
            if available:
                await self.start_thread_task(delegation_id, revision, context, request, channel)
                return
        if routed:
            await self.start_task(delegation_id, revision, context, request, deliver_to=channel.target,
                                  ack=P.ack_channel_post(channel.label))
            return
        await self.start_task(delegation_id, revision, context, request)

    async def match_conversation(self, request: str) -> continuity.Conversation | None:
        if not request or not self.rt.settings()["continuity"]["enabled"]:
            return None
        try:
            options = await asyncio.to_thread(continuity.recent_conversations, self.rt.state_db)
            return continuity.match(request, options)
        except Exception as exc:  # never let matching break a request
            logger.warning("speakeasy: conversation match failed: %s", type(exc).__name__)
            return None

    async def follow_up(self, delegation_id: str, revision: int, context: str, part: router.Part) -> None:
        """Add to an open task when its run still accepts guidance; otherwise continue in that
        task's own session with a run that knows the earlier request and result."""
        with self.interaction.lock:
            target = self.interaction.runs.get(part.task_id or "")
            run_id = target.run_id if target else None
            status = target.status if target else None
            key = target.idem_key if target else None
        if target is None or key is None:
            await self.start_task(delegation_id, revision, context, part.request)
            return
        earlier = notice_text(self.store.request_text(key), 200) or "an earlier request"
        placed = self.store.continued_for(key)
        opening_thread = placed is None and not run_id and status in ACTIVE_RUN_STATES and target.deliver_to
        waited = 0.0
        while opening_thread and placed is None and waited < THREAD_OPEN_WAIT_S:
            await asyncio.sleep(1.0)  # a thread task that just started: its session appears in seconds
            waited += 1.0
            placed = self.store.continued_for(key)
        if placed is not None:
            conv = await asyncio.to_thread(continuity.conversation_by_session, self.rt.state_db, placed["session_id"])
            if conv is not None:
                joins = status in ACTIVE_RUN_STATES
                await self.start_continuity_task(delegation_id, revision, context, part.request, conv,
                                                 joins=target if joins else None)
                return
        if run_id and status in {"running", "working"}:
            accepted = await asyncio.to_thread(self.hermes.steer, run_id, P.steer_text(self.names, part.request))
            if accepted:
                self.store.progress(key, "milestone", f"You added: {notice_text(part.request, 200)}")
                with self.interaction.lock:
                    self.interaction.latest_delegation_id = target.delegation_id
                self.publish()
                await self.append("session.thinking.append", delegation_id, P.added_to_task_note(earlier))
                self.handoff_at.pop(delegation_id, None)
                return
        work = self.store.work(idem_key=key) or {}
        result = notice_text((work.get("result") or {}).get("spoken"), 300)
        focus = P.follow_up_focus(part.request, earlier, result, status)
        await self.start_task(delegation_id, revision, context, f"{part.request} (following up: {earlier})",
                              focus=focus, session_id=self.store.session_for(key), replaces=target.delegation_id,
                              deliver_to=target.deliver_to)

    def _register(self, task_id: str, backend: BackendRun, replaces: str | None = None) -> str | None:
        replaced_key = None
        with self.interaction.lock:
            old = self.interaction.runs.get(replaces or "")
            if old is not None and old.status not in ACTIVE_RUN_STATES and old.delegation_id != task_id:
                # A follow-up to a finished task replaces it: same slot in the list, one row.
                replaced_key = old.idem_key
                self.interaction.runs = {(task_id if key == replaces else key): (backend if key == replaces else run)
                                         for key, run in self.interaction.runs.items() if key != task_id}
            else:
                self.interaction.runs[task_id] = backend
            self.interaction.latest_delegation_id = task_id
        return replaced_key

    async def start_task(self, task_id: str, revision: int, context: str, request: str,
                         focus: str | None = None, voice_id: str | None = None,
                         session_id: str | None = None, replaces: str | None = None,
                         deliver_to: str | None = None, ack: str | None = None) -> None:
        idem = self._idem(task_id, revision)
        backend = BackendRun(task_id, revision, idem, voice_id=voice_id, deliver_to=deliver_to)
        delegation_id = backend.say_id
        replaced_key = self._register(task_id, backend, replaces)
        if replaced_key:
            self.store.dismiss_key(replaced_key)
        self.publish()
        if not session_id:
            session_id = TASK_SESSION_PREFIX + hashlib.sha256(idem.encode()).hexdigest()[:24]
        label = self.rt.channel_label(deliver_to) if deliver_to else self.rt.delivery_label()
        prompt = P.build_task_prompt(self.names, revision, context, focus, label)
        state, known_run = self.store.reserve_run(idem, self.interaction.interaction_id, task_id, revision)
        if state != "created":
            if known_run:
                with self.interaction.lock:
                    backend.run_id, backend.status = known_run, state
            self.publish()
            return
        self.store.set_session(idem, session_id)
        self.record_timing(task_id, idem)
        if request:
            self.store.progress(idem, "request", request)
            self.store.set_title(idem, short_title(request))
        with self.interaction.lock:
            others = [r.idem_key for r in self.interaction.runs.values()
                      if r.delegation_id != task_id and r.status in ACTIVE_RUN_STATES]
        parallel = [r for r in (notice_text(self.store.request_text(k), 80) for k in others[-4:]) if r]
        if voice_id is None:
            await self.append("session.thinking.append", delegation_id, P.work_started_note(parallel))
            await self.say_where(delegation_id, ack if deliver_to and not replaces else None)
        try:
            run_id = await asyncio.to_thread(self.hermes.start_run, prompt, idem, session_id)
            self.store.update_run(idem, run_id, "running")
            with self.interaction.lock:
                backend.run_id, backend.status = run_id, "running"
            self.publish()
            loop = asyncio.get_running_loop()
            terminal_seen = await asyncio.to_thread(
                self.hermes.events, run_id,
                lambda event: asyncio.run_coroutine_threadsafe(self.handle_hermes_event(backend, event), loop).result())
            if not terminal_seen:
                await self.reconcile_stream_end(backend)
        except Exception as exc:
            if backend.run_id:
                try:
                    await self.reconcile_stream_end(backend, exc)
                    return
                except Exception as reconcile_exc:
                    exc = reconcile_exc
            self.store.update_run(idem, None, "failed")
            self.store.progress(idem, "result", "Work failed; details unavailable")
            with self.interaction.lock:
                backend.status, backend.error = "failed", str(exc)[:240]
            self.publish()
            if backend.run_id:
                self.notices.stopped(backend.run_id, "failed", self.store.request_text(idem))
            await self.append("session.commentary.append", delegation_id, P.FAILED_SPOKEN)

    async def start_continuity_task(self, task_id: str, revision: int, context: str, request: str,
                                    conv: continuity.Conversation, joins: "BackendRun | None" = None) -> None:
        """Run the request inside an existing Hermes conversation's own session: it sees that
        conversation's history, and the reply posts back to that chat."""
        idem = self._idem(task_id, revision)
        backend = BackendRun(task_id, revision, idem, deliver_to=joins.deliver_to if joins else None)
        delegation_id = backend.say_id
        if joins is None:
            self._register(task_id, backend)
        else:
            # Part of a task still running: no new row. It goes into the same thread after that
            # turn and takes over the row then, so the panel shows one task with one final answer.
            earlier = notice_text(self.store.request_text(joins.idem_key), 200) or "the task"
            self.store.progress(joins.idem_key, "milestone", f"You added: {notice_text(request, 200)}")
            await self.append("session.thinking.append", delegation_id, P.added_to_task_note(earlier))
            self.handoff_at.pop(delegation_id, None)
        self.publish()
        state, known_run = self.store.reserve_run(idem, self.interaction.interaction_id, task_id, revision)
        if state != "created":
            if known_run:
                with self.interaction.lock:
                    backend.run_id, backend.status = known_run, state
            self.publish()
            return
        where = notice_text(conv.where, 100) or "that"
        self.record_timing(task_id, idem)
        self.store.set_continued(idem, conv.session_id, conv.where)
        self.store.progress(idem, "request", request)
        self.store.set_title(idem, short_title(request))
        self.store.progress(idem, "milestone", f"Continuing in {where}")
        if joins is not None:
            self.store.hide_key(idem)
        self.publish()
        if joins is None:
            await self.append("session.thinking.append", delegation_id, P.continuing_in_note(where))
            self.handoff_at.pop(delegation_id, None)
        waited = 0
        while joins is not None:  # hold until the first turn is done, then take over its row
            with self.interaction.lock:
                first_done = joins.status not in ACTIVE_RUN_STATES
            if first_done or waited >= CONTINUITY_WAIT_S:
                self.store.unhide_key(idem)
                replaced_key = self._register(task_id, backend, replaces=joins.delegation_id)
                if replaced_key:
                    self.store.dismiss_key(replaced_key)
                self.store.set_title(idem, self.store.title(joins.idem_key) or short_title(request))
                self.publish()
                joins = None
                break
            await asyncio.sleep(CONTINUITY_POLL_S)
            waited += CONTINUITY_POLL_S
        waited = 0
        while await asyncio.to_thread(continuity.session_busy, self.rt.state_db, conv.session_id):
            if waited == 0:
                self.store.user_progress(idem, "Queued", f"Waiting for current work in {where} to finish")
                self.publish()
            if waited >= CONTINUITY_WAIT_S:
                break
            await asyncio.sleep(CONTINUITY_POLL_S)
            waited += CONTINUITY_POLL_S
        try:
            await asyncio.to_thread(continuity.ensure_alias, conv)
            message = P.continuation_message(self.names, request, notice_text(request, 200) or "voice request")
            loop = asyncio.get_running_loop()
            final: dict[str, Any] = {}

            def on_event(name: str, payload: dict[str, Any]) -> None:
                if name == "run.started" and isinstance(payload.get("run_id"), str) and ID_RE.fullmatch(payload["run_id"]):
                    run_id = payload["run_id"]
                    self.store.update_run(idem, run_id, "running")
                    with self.interaction.lock:
                        backend.run_id, backend.status = run_id, "running"
                    asyncio.run_coroutine_threadsafe(self._publish_async(), loop)
                elif name in {"tool.started", "assistant.commentary"}:
                    event = {"event": "tool.started" if name == "tool.started" else "message.interim",
                             "tool": payload.get("tool_name"), "preview": payload.get("preview"),
                             "text": payload.get("text")}
                    asyncio.run_coroutine_threadsafe(self.handle_hermes_event(backend, event), loop).result()
                elif name == "assistant.completed":
                    final["text"] = payload.get("content") or ""
                elif name.startswith("run.") and name != "run.started":
                    final["status"] = name.split(".", 1)[1]

            terminal = await asyncio.to_thread(continuity.stream_session_chat, self.hermes.base, self.rt.hermes_key(),
                                               conv, message, on_event)
            status = final.get("status") or ("completed" if terminal and final.get("text") else "ambiguous")
            output = P.without_voice_header(final.get("text", ""))
            if status == "ambiguous":
                if backend.run_id:
                    await self.reconcile_stream_end(backend)
                    return
                raise ServiceError(502, "conversation stream ended without a result")
            await self.handle_hermes_event(backend, {"event": f"run.{status}", "output": output, "continued": True})
        except Exception as exc:
            self.store.update_run(idem, None, "failed")
            self.store.progress(idem, "result", "Work failed; details unavailable")
            with self.interaction.lock:
                backend.status, backend.error = "failed", str(exc)[:240]
            self.publish()
            await self.append("session.commentary.append", delegation_id, P.continuing_failed_note(where))

    async def start_thread_task(self, task_id: str, revision: int, context: str, request: str,
                                channel: channels.Channel) -> None:
        """Open a new thread in the channel and run the task there, as if typed in it: the user can
        follow up in that thread, and the call hears its first answer. Falls back to running here
        and posting the answer to the channel when Hermes can't open the thread."""
        from .threads import ThreadError
        idem = self._idem(task_id, revision)
        backend = BackendRun(task_id, revision, idem, deliver_to=channel.target)
        delegation_id = backend.say_id
        self._register(task_id, backend)
        self.publish()
        summary = notice_text(request, 200) or "voice request"
        message = P.thread_task_message(self.names, request, summary, context)
        try:
            opened = await asyncio.to_thread(self.rt.threads.open, channel.target, message=message,
                                             title=short_title(request) or "Voice task", delivery_id=idem)
        except (ThreadError, OSError) as exc:
            logger.info("speakeasy: new thread unavailable, posting to the channel instead (%s)", exc)
            await self.start_task(task_id, revision, context, request, deliver_to=channel.target,
                                  ack=P.ack_channel_post(channel.label))
            return
        state, _ = self.store.reserve_run(idem, self.interaction.interaction_id, task_id, revision)
        if state != "created":
            self.publish()
            return
        where = f"a new {channel.label} thread"
        self.record_timing(task_id, idem)
        self.store.progress(idem, "request", request)
        self.store.set_title(idem, short_title(request))
        self.store.update_run(idem, None, "running")
        self.store.progress(idem, "milestone", f"Started in {where}")
        with self.interaction.lock:
            backend.status = "running"
        self.publish()
        await self.say_where(delegation_id, P.ack_channel_thread(channel.label))

        def on_session(session_id: str) -> None:
            # Follow-ups to this task continue inside the thread (thread continuity).
            self.store.set_continued(idem, session_id, f"{channel.label} thread")

        def on_title(title: str) -> None:
            # Hermes names the thread after its first turn; show the same name in the panel.
            self.store.set_title(idem, title)
            self.publish()

        try:
            answer = await asyncio.to_thread(self.rt.threads.wait, opened, on_session, on_title)
        except Exception as exc:
            answer, backend.error = None, type(exc).__name__
        if answer is None:
            self.store.user_progress(idem, "In the thread", f"The answer will land in {where}")
            self.store.update_run(idem, None, "ambiguous")
            with self.interaction.lock:
                backend.status = "ambiguous"
            self.publish()
            return
        await self.handle_hermes_event(backend, {"event": "run.completed", "output": P.without_voice_header(answer),
                                                 "continued": True})

    async def reconcile_stream_end(self, backend: BackendRun, cause: Exception | None = None) -> None:
        if not backend.run_id:
            raise ServiceError(502, "Hermes stream ended before a run ID was assigned")
        try:
            result = await asyncio.to_thread(self.hermes.get_run, backend.run_id)
        except Exception as exc:
            result, cause = {}, cause or exc
        status = result.get("status")
        if status in TERMINAL:
            event = {"event": f"run.{status}"}
            if "output" in result:
                event["output"] = result["output"]
            await self.handle_hermes_event(backend, event)
            return
        detail = "Hermes event stream ended before a terminal event" + (f": {type(cause).__name__}" if cause else "")
        self.store.update_run(backend.idem_key, None, "ambiguous")
        with self.interaction.lock:
            backend.status, backend.approval, backend.error = "ambiguous", None, detail
        self.publish()
        self.notices.stopped(backend.run_id, "ambiguous", self.store.request_text(backend.idem_key))
        await self.append("session.commentary.append", backend.say_id, P.lost_track_note(self.names))

    async def handle_hermes_event(self, backend: BackendRun, event: dict[str, Any]) -> None:
        try:
            await self._handle_hermes_event(backend, event)
        finally:
            self.publish()

    async def _handle_hermes_event(self, backend: BackendRun, event: dict[str, Any]) -> None:
        idem, delegation_id = backend.idem_key, backend.say_id
        kind = event.get("event")
        if kind in {"tool.started", "tool.start", "message.interim"}:
            with self.interaction.lock:
                if backend.status != "ambiguous":
                    backend.status = "working"
            if backend.status != "ambiguous":
                self.store.update_run(idem, None, "working")
            if kind == "message.interim":
                progress = interim_progress(event)
                if progress:
                    self.store.user_progress(idem, *progress)
                    self.store.progress(idem, "milestone", progress[1])
                    await self.append("session.thinking.append", delegation_id, progress[1])
                    await self.maybe_speak_progress(backend, progress[0])
            elif backend.status != "ambiguous":
                derived = derive_tool_status(event.get("tool"), event.get("preview"))
                if derived:
                    self.store.tool_progress(idem, *derived)
        elif kind == "approval.request":
            request_id = event.get("request_id")
            if not isinstance(request_id, str) or not ID_RE.fullmatch(request_id):
                return
            description = safe_user_text(event.get("description") or event.get("reason"), 500)
            safe = {"request_id": request_id, "description": description or "Consequential action",
                    "choices": ["once", "deny"]}
            with self.interaction.lock:
                if backend.status == "ambiguous":
                    return
                backend.approval, backend.status = safe, "waiting_for_approval"
            self.store.update_run(idem, None, "waiting_for_approval")
            self.store.progress(idem, "milestone", "Waiting for your approval")
            if not self.call_connected():
                self.notices.needs_you(request_id, safe["description"])
            await self.append("session.commentary.append", delegation_id, P.approval_note(self.names))
        elif kind and kind.startswith("run."):
            status = kind.split(".", 1)[1]
            self.store.update_run(idem, None, status)
            if status not in TERMINAL:
                return
            result = split_result(event.get("output"), self.rt.image_roots())
            drafts = (result or {}).pop("_email_drafts", None) or []
            self.store.set_result(idem, result)
            safe_output = safe_user_text(result["full"], 4000) if result else None
            self.store.progress(idem, "result", safe_output or {
                "completed": "Work complete", "cancelled": "Work stopped", "failed": "Work failed",
                "interrupted": "Work interrupted"}.get(status, "Work ended"))
            with self.interaction.lock:
                backend.status, backend.approval = status, None
            stored_drafts = [self.store.add_draft(idem, self.store.session_for(idem), d) for d in drafts]
            head = self.interaction.head()
            with head.lock:
                latest = head.runs.get(head.latest_delegation_id or "")
                is_latest = latest is not None and latest.say_id == delegation_id
                siblings = [r for r in head.runs.values()
                            if r is not backend and r.say_id == delegation_id and r.status in ACTIVE_RUN_STATES]
            if status != "completed" and backend.run_id:
                self.notices.stopped(backend.run_id, status, self.store.request_text(idem))
            if status == "completed" and backend.run_id and result and not event.get("continued"):
                self.notices.answered(backend.run_id, delivery_text(result), backend.deliver_to)
            for draft in stored_drafts:
                if not self.call_connected():
                    self.notices.draft_waiting(draft["draft_id"], draft.get("subject"))
            if status == "completed":
                spoken = result["spoken"] if result else "Done."
                background = P.result_background(self.store.title(idem) or self.store.request_text(idem),
                                                 result.get("full") if result else None, spoken)
                if background:
                    await self.append("session.thinking.append", delegation_id, background)
                if stored_drafts:
                    d = stored_drafts[-1]
                    await self.append("session.thinking.append", delegation_id,
                                      P.draft_waiting_note(self.names, d.get("subject"), d.get("to") or []))
                if is_latest and backend.voice_id:
                    part = notice_text(self.store.request_text(idem), 140) or "one part"
                    await self.append("session.commentary.append", delegation_id,
                                      P.result_for_part(part, spoken, bool(siblings)))
                elif is_latest:
                    await self.append("session.commentary.append", delegation_id, spoken)
                else:
                    request = notice_text(self.store.request_text(idem), 140) or "an earlier request"
                    await self.append("session.commentary.append", delegation_id,
                                      P.earlier_task_finished(self.names, request, spoken))
            elif status == "cancelled":
                await self.append("session.commentary.append", delegation_id, P.STOPPED_SPOKEN)
            else:
                await self.append("session.commentary.append", delegation_id, P.ended_without_result(status))


def new_event_id() -> str:
    return "speakeasy_" + secrets.token_hex(12)
