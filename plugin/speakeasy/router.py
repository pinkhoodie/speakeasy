"""Task routing without a third-party classifier.

Every spoken request is one new task unless it is marked as a follow-up to an open task:
- the voice model marks it (a handoff that names a ``task_id`` / ``follow_up_task_id`` of an open
  task), or
- it is unmistakably a follow-up: it carries a follow-up cue ("make it", "change that", "also add")
  and either only one task is open, or it shares clear words with exactly one open task.

Anything unclear stays a new task, which is the safe behavior (it never hijacks an open task).
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any

MAX_OPEN_TASKS = 8
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
