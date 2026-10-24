"""Honest receipts for what the user adds to a running task by voice.

``POST /v1/runs/{id}/steer`` answering ``accepted: true`` only means Hermes queued the text on the
running agent (``agent.steer`` stores it as ``_pending_steer``). Nothing on the run's event stream says
when the agent actually took it. What Hermes does do, read-only from here:

- At the end of a tool batch, or just before its next model call, the agent drains the queued text
  and appends it as its own ``role: user`` row (``display_kind`` ``steer``, wrapped in the out-of-band
  marker) after the newest tool result. That row is flushed to the session transcript in
  ``<HERMES_HOME>/state.db``. Its appearance there, newer than the moment the steer was sent, is the
  evidence that the model saw it: it is in the very message list of its next call.
- A steer still queued when the turn ends comes back as ``pending_steer`` on the terminal run event
  (and on ``GET /v1/runs/{id}``). A stop clears it without delivering it.

So a follow-up is ``queued`` once Hermes accepted it, ``landed`` only once its text is in a user row of
the task's session (or a session compaction split off it) written after it was sent, and ``not_read``
when the run ended with it still pending, or ended without it ever reaching the transcript.
Everything here fails closed: an unreadable database never counts as landed.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

QUEUED, LANDED, NOT_READ = "queued", "landed", "not_read"
STATES = (QUEUED, LANDED, NOT_READ)
# Compaction can continue a session under a child id: follow at most this many generations.
MAX_GENERATIONS = 4


def _flat(text: Any) -> str:
    return " ".join(str(text or "").split())


def _sessions(db: sqlite3.Connection, session_id: str) -> list[str]:
    try:
        rows = db.execute(
            "WITH RECURSIVE tree(id, depth) AS (SELECT ?, 0 UNION ALL "
            "SELECT s.id, tree.depth + 1 FROM sessions s JOIN tree ON s.parent_session_id = tree.id "
            "WHERE tree.depth < ?) SELECT id FROM tree", (session_id, MAX_GENERATIONS)).fetchall()
    except sqlite3.Error:
        return [session_id]
    return list(dict.fromkeys(r[0] for r in rows if r and r[0])) or [session_id]


def landed(state_db: Path, session_id: str | None, after_id: int, body: str) -> bool:
    """True only when ``body`` (the exact steer text sent) is in a user row of the task's session
    written after message ``after_id``. Whitespace is compared flattened (Hermes strips the text and
    may join several queued steers with newlines into one row)."""
    want = _flat(body)
    if not session_id or not want:
        return False
    try:
        db = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return False
    try:
        sessions = _sessions(db, session_id)
        marks = ",".join("?" * len(sessions))
        rows = db.execute(
            f"SELECT content FROM messages WHERE session_id IN ({marks}) AND id>? AND role='user' "
            "ORDER BY id LIMIT 400", (*sessions, int(after_id or 0))).fetchall()
    except sqlite3.Error:
        return False
    finally:
        db.close()
    return any(want in _flat(content) for (content,) in rows)


def still_pending(pending_steer: Any, body: str) -> bool:
    """The run ended and handed this text back undelivered (``pending_steer``)."""
    want = _flat(body)
    return bool(want) and want in _flat(pending_steer)
