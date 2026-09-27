"""Task routing: follow-up vs new, splitting a compound request, and the topical channel.

``decide`` makes ONE call per handoff to the user's own Hermes auxiliary model (task
``speakeasy_router``; the user picks its model in Hermes: ``hermes model`` → auxiliary tasks, or
``auxiliary.speakeasy_router`` in config.yaml). It returns strict JSON
``{"follow_up_task_id": id|null, "parts": [1-4 self-contained requests], "channel": label|null}``.
On timeout (3 s), error or invalid JSON it falls back to ``route`` below, and never blocks a task.

Fallback (``route``): every spoken request is one new task unless it is marked as a follow-up to an open task:
- the voice model marks it (a handoff that names a ``task_id`` / ``follow_up_task_id`` of an open
  task), or
- it is unmistakably a follow-up: it carries a follow-up cue ("make it", "change that", "also add")
  and either only one task is open, or it shares clear words with exactly one open task.

Anything unclear stays a new task, which is the safe behavior (it never hijacks an open task).
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import logging
import re
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

MAX_OPEN_TASKS = 8
MAX_PARTS = 4
AUX_TASK = "speakeasy_router"
AUX_DISPLAY_NAME = "Speakeasy task routing"
AUX_DESCRIPTION = "Decides, per spoken request, follow-up vs new task, splits compound asks, and picks a chat channel."
ROUTE_TIMEOUT_S = 3.0
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="speakeasy-router")
NEW = "new"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_FOLLOW_UP_CUE = re.compile(
    r"(?i)^(?:and |also |oh,? |actually,? |wait,? |no,? |okay,? |ok,? )*(?:"
    r"make (?:it|that|them)|change (?:it|that|them)|instead|also (?:add|include|check|send|make)|"
    r"add (?:to that|that)|cancel (?:it|that)|revise (?:it|that)|update (?:it|that)|"
    r"for (?:that|it)|about (?:that|it)|on (?:that|it)|the same|as well|too\b|"
    r"what about|and (?:then|also)|same (?:thing|one)|send (?:it|that)|reword|shorter|longer)")
_STOP = set("""a an the and or to of in on for with at by from is are was be this that it its my our your me we
you i he she they them do does did can could would should will just please tell ask go let lets get have has had
about into over up out so then than now new thing things make change also add check what how""".split())


@dataclasses.dataclass(frozen=True)
class OpenTask:
    task_id: str
    request: str
    status: str
    result: str = ""


@dataclasses.dataclass(frozen=True)
class Part:
    kind: str              # "new" | "follow_up"
    request: str
    task_id: str | None = None


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def route(request: str, tasks: list[OpenTask], marked_task_id: Any = None) -> list[Part]:
    """One Part: the request as a new task, or a follow-up to one open task."""
    request = (request or "").strip()
    open_tasks = tasks[-MAX_OPEN_TASKS:]
    if isinstance(marked_task_id, str) and TASK_ID_RE.fullmatch(marked_task_id):
        if any(t.task_id == marked_task_id for t in open_tasks):
            return [Part("follow_up", request, marked_task_id)]
    if not request or not open_tasks or not _FOLLOW_UP_CUE.search(request):
        return [Part(NEW, request)]
    if len(open_tasks) == 1:
        return [Part("follow_up", request, open_tasks[0].task_id)]
    asked = _words(request)
    scored = sorted(((len(asked & _words(t.request + " " + t.result)), i, t) for i, t in enumerate(open_tasks)),
                    key=lambda item: (item[0], item[1]), reverse=True)
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return [Part("follow_up", request, scored[0][2].task_id)]
    # "make it for four" with several open tasks: the newest task is the natural referent only when
    # the request names nothing else.
    if not asked:
        return [Part("follow_up", request, open_tasks[-1].task_id)]
    return [Part(NEW, request)]


# -- the routing model ------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Topic:
    label: str
    topic: str


@dataclasses.dataclass(frozen=True)
class Decision:
    parts: list[Part]
    channel: str | None = None     # an opted-in channel label picked by topic, or None
    source: str = "fallback"       # "model" | "marked" | "fallback"
    latency_ms: int = 0


def route_messages(request: str, tasks: list[OpenTask], topics: list[Topic]) -> list[dict[str, str]]:
    open_lines = "\n".join(f"- {t.task_id}: {t.request[:200]} ({t.status})" for t in tasks[-MAX_OPEN_TASKS:]) or "none"
    channel_lines = "\n".join(f"- {c.label}: {c.topic or 'no description'}" for c in topics) or "none"
    return [
        {"role": "system", "content":
            "You route one spoken request for a voice assistant. Reply with strict JSON only, no prose: "
            '{"follow_up_task_id": string or null, "parts": [strings], "channel": string or null}. '
            "follow_up_task_id: the id of an open task ONLY when the request clearly adds to, changes, corrects "
            "or asks about that task; else null. parts: when not a follow-up, the request as 1 to 4 independent, "
            "self-contained asks (split only clearly separate asks; keep one ask whole; each part must make sense "
            "alone). channel: the label of the channel whose description clearly fits, else null."},
        {"role": "user", "content": f"Open tasks:\n{open_lines}\n\nChannels:\n{channel_lines}\n\n"
                                    f"Request: {request[:1500]}"},
    ]


def parse_decision(text: Any, request: str, tasks: list[OpenTask], topics: list[Topic]) -> Decision | None:
    """Validate the model's JSON strictly; None when anything is off (the caller falls back)."""
    if not isinstance(text, str):
        return None
    match = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(match.group(0) if match else text)
    except (ValueError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    labels = {c.label.lower().lstrip("#"): c.label for c in topics}
    raw_channel = data.get("channel")
    if raw_channel is not None and not isinstance(raw_channel, str):
        return None
    channel = labels.get((raw_channel or "").lower().lstrip("#")) if raw_channel else None
    follow = data.get("follow_up_task_id")
    if follow is not None:
        if not isinstance(follow, str) or not any(t.task_id == follow for t in tasks[-MAX_OPEN_TASKS:]):
            return None
        return Decision([Part("follow_up", request, follow)], None, "model")
    parts = data.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS:
        return None
    clean = [" ".join(p.split())[:600] for p in parts if isinstance(p, str) and p.strip()]
    if len(clean) != len(parts):
        return None
    if len(clean) == 1:
        clean = [request]  # one ask: keep the user's own words
    return Decision([Part(NEW, p) for p in clean], channel, "model")


_COMPOUND = re.compile(r"(?i)\b(?:and|also|plus|then|as well)\b|[,;]")


def needs_model(request: str, tasks: list[OpenTask], topics: list[Topic]) -> bool:
    """Skip the model when there is nothing to decide: no open task to follow, no channel to pick,
    and nothing that could split. Keeps the spoken acknowledgement instant for the plain case."""
    return bool(request) and bool(tasks or topics or _COMPOUND.search(request))


def aux_call(messages: list[dict[str, str]], timeout: float = ROUTE_TIMEOUT_S) -> str | None:
    """One request through Hermes' auxiliary client, in-process (the plugin runs in the gateway)."""
    try:
        from agent.auxiliary_client import call_llm, extract_content_or_reasoning  # type: ignore
    except Exception:
        return None
    response = call_llm(task=AUX_TASK, messages=messages, temperature=0, max_tokens=300, timeout=timeout)
    return extract_content_or_reasoning(response)


def decide(request: str, tasks: list[OpenTask], marked_task_id: Any = None, topics: list[Topic] | None = None,
           call: Callable[[list[dict[str, str]]], str | None] | None = None,
           timeout: float = ROUTE_TIMEOUT_S) -> Decision:
    """Route one handoff. A task id the voice model marked wins outright (no model call)."""
    request = (request or "").strip()
    topics = topics or []
    open_tasks = tasks[-MAX_OPEN_TASKS:]
    if isinstance(marked_task_id, str) and any(t.task_id == marked_task_id for t in open_tasks):
        return Decision([Part("follow_up", request, marked_task_id)], None, "marked")
    started = time.monotonic()
    decision = None
    if needs_model(request, open_tasks, topics):
        future = _EXECUTOR.submit(call or aux_call, route_messages(request, open_tasks, topics))
        try:
            decision = parse_decision(future.result(timeout=timeout), request, open_tasks, topics)
        except Exception as exc:  # timeout or provider error: the rules below
            logger.info("speakeasy: routing model unavailable, using the fallback (%s)", type(exc).__name__)
    latency = int((time.monotonic() - started) * 1000)
    logger.debug("speakeasy: routing took %d ms (%s)", latency, "model" if decision else "fallback")
    if decision is None:
        return Decision(route(request, tasks, marked_task_id), None, "fallback", latency)
    return dataclasses.replace(decision, latency_ms=latency)


def routing_model(config: dict[str, Any] | None = None) -> str:
    """Which model routing uses, for display: auxiliary.speakeasy_router in the user's config."""
    if config is None:
        try:
            from hermes_cli.config import load_config_readonly  # type: ignore
            config = load_config_readonly()
        except Exception:
            config = {}
    aux = (config or {}).get("auxiliary") if isinstance(config, dict) else None
    block = aux.get(AUX_TASK) if isinstance(aux, dict) else None
    block = block if isinstance(block, dict) else {}
    provider = str(block.get("provider") or "auto").strip() or "auto"
    model = str(block.get("model") or "").strip()
    if provider == "auto" and not model:
        return "Hermes auxiliary default (auto)"
    return f"{provider} · {model}" if model else provider
