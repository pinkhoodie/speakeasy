"""Continue an existing Hermes conversation from voice ("in the trip planning chat, also book ...").

Candidates are the user's recent live Hermes
sessions on any messaging platform (a Discord thread, a Telegram chat, a Slack channel, ...), read
read-only from ``<HERMES_HOME>/state.db``. Matching is deterministic (no third-party classifier):
the request must carry a continuation cue and clearly share words with exactly one recent
conversation's title/name.

The turn runs through ``/api/sessions/{id}/chat/stream`` with an ``X-Hermes-Session-Key`` alias,
the only API path that fans a completed turn back out to the native chat. The alias is added once
through Hermes' own config writer (comment-preserving) when running inside the gateway.
"""
from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

MAX_CANDIDATES = 40
RECENT_DAYS = 21
MIN_OVERLAP = 2
# Platforms whose sessions are the user's own chats. Internal sources never qualify.
EXCLUDED_SOURCES = {"cron", "subagent", "cli", "tool", "api_server", "webhook", "oneshot", "voice", "acp"}
ALIAS_PREFIX = "speakeasy:"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_:@.+=-]{1,128}$")

_CONTINUE_CUES = re.compile(
    r"\b(thread|channel|chat|conversation|build|project|session|over in|in the|pick up|continue|same place|there)\b", re.I)
_STOP = set("""a an the and or to of in on for with at by from is are was be this that it its
my our your me we you i he she they them do does did can could would should will just please
tell ask go let lets get have has had about into over up out so then than now new thing things
thread channel chat conversation session project build continue pick same place there also""".split())


@dataclasses.dataclass(frozen=True)
class Conversation:
    session_id: str
    platform: str
    chat_id: str
    chat_type: str
    thread_id: str
    user_id: str
    parent_chat_id: str
    name: str            # platform chat name, e.g. "Server / #research / Topic"
    title: str           # Hermes session title
    last_active: float

    @property
    def label(self) -> str:
        """Short name to show and say: the Hermes session title, else the last chat-name segment."""
        topic = self.name.split(" / ")[-1].strip() if self.name else ""
        return (self.title or topic or self.platform)[:80]

    @property
    def where(self) -> str:
        return f"{self.platform.title()} \"{self.label}\""

    @property
    def session_key(self) -> str:
        return f"{ALIAS_PREFIX}{self.platform}:{self.thread_id or self.chat_id}"

    def alias(self) -> dict[str, str]:
        out = {"platform": self.platform, "chat_id": self.chat_id, "chat_type": self.chat_type or "dm"}
        for key in ("thread_id", "user_id", "parent_chat_id"):
            value = getattr(self, key)
            if value:
                out[key] = value
        return out


def _open(state_db: Path) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None


def _from_row(session_id: str, source: str, chat_type: str | None, thread_id: str | None,
              origin_json: str | None, title: str, last: float) -> Conversation | None:
    try:
        origin = json.loads(origin_json or "{}")
    except ValueError:
        return None
    if not isinstance(origin, dict):
        return None
    chat_id = str(origin.get("chat_id") or "")
    if not SAFE_ID_RE.fullmatch(chat_id):
        return None
    fields = {k: str(origin.get(k) or "") for k in ("user_id", "parent_chat_id")}
    thread = str(thread_id or origin.get("thread_id") or "")
    if any(v and not SAFE_ID_RE.fullmatch(v) for v in (*fields.values(), thread)):
        return None
    return Conversation(session_id, source, chat_id, str(chat_type or origin.get("chat_type") or "dm"), thread,
                        fields["user_id"], fields["parent_chat_id"], str(origin.get("chat_name") or "")[:200],
                        (title or "")[:200], float(last or 0))


def recent_conversations(state_db: Path, *, days: int = RECENT_DAYS, limit: int = MAX_CANDIDATES,
                         now: float | None = None) -> list[Conversation]:
    """Live messaging-platform sessions active recently, newest first (read-only)."""
    since = (now or time.time()) - days * 86400
    db = _open(state_db)
    if db is None:
        return []
    marks = ",".join("?" * len(EXCLUDED_SOURCES))
    try:
        rows = db.execute(
            f"""SELECT s.id, s.source, s.chat_type, s.thread_id, s.origin_json, coalesce(s.title,''), max(m.timestamp)
               FROM sessions s JOIN messages m ON m.session_id = s.id
               WHERE s.ended_at IS NULL AND s.source NOT IN ({marks}) AND s.origin_json IS NOT NULL
                 AND m.timestamp > ?
               GROUP BY s.id ORDER BY max(m.timestamp) DESC LIMIT ?""",
            (*sorted(EXCLUDED_SOURCES), since, limit)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    out, seen = [], set()
    for row in rows:
        conv = _from_row(*row)
        if conv is None or conv.session_key in seen:
            continue
        seen.add(conv.session_key)
        out.append(conv)
    return out


def conversation_by_session(state_db: Path, session_id: str) -> Conversation | None:
    db = _open(state_db)
    if db is None:
        return None
    try:
        row = db.execute(
            """SELECT s.id, s.source, s.chat_type, s.thread_id, s.origin_json, coalesce(s.title,''),
                      coalesce((SELECT max(timestamp) FROM messages m WHERE m.session_id = s.id), s.started_at, 0)
               FROM sessions s WHERE s.id=?""", (session_id,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    return _from_row(*row) if row else None


def session_busy(state_db: Path, session_id: str, *, window_s: float = 900, now: float | None = None) -> bool:
    """True while a conversation looks mid-turn (newest message is a user turn, a tool result, or a
    tool call, and recent). Voice work waits rather than colliding with a turn typed in the chat."""
    db = _open(state_db)
    if db is None:
        return False
    try:
        row = db.execute("SELECT role, tool_calls, timestamp FROM messages WHERE session_id=? "
                         "ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
    except sqlite3.Error:
        return False
    finally:
        db.close()
    if not row:
        return False
    role, tool_calls, ts = row
    open_turn = role in {"user", "tool"} or (role == "assistant" and bool(tool_calls))
    return open_turn and ((now or time.time()) - float(ts or 0)) < window_s


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def match(request: str, conversations: list[Conversation]) -> Conversation | None:
    """Deterministic: a continuation cue, then one clear best overlap of at least MIN_OVERLAP words.

    Status questions ("how is X going") are answered by reading, not by continuing a conversation.
    """
    request = (request or "").strip()
    if not request or not _CONTINUE_CUES.search(request):
        return None
    if re.search(r"(?i)\b(how is|how's|where are we|what did we|status of|any update)\b", request):
        return None
    asked = _words(request)
    if not asked:
        return None
    scored = sorted(((len(asked & (_words(c.name) | _words(c.title))), c.last_active, c) for c in conversations),
                    key=lambda item: (item[0], item[1]), reverse=True)
    if not scored or scored[0][0] < MIN_OVERLAP:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None  # ambiguous: two conversations match equally well
    return scored[0][2]


def ensure_alias(conv: Conversation) -> bool:
    """Add a `session_key_aliases` entry for this conversation via Hermes' own config writer, so
    the reply fans out to the native chat. Returns False when not possible (then the turn still runs,
    it only is not mirrored)."""
    try:
        from hermes_cli.config import atomic_config_write, get_config_path, read_user_config_raw  # type: ignore
    except Exception:
        return False
    try:
        path = get_config_path()
        raw = read_user_config_raw(path)
        aliases = dict(raw.get("session_key_aliases") or {})
        if aliases.get(conv.session_key) == conv.alias():
            return True
        aliases[conv.session_key] = conv.alias()
        atomic_config_write(path, {"session_key_aliases": aliases})
        return True
    except Exception:
        return False


def stream_session_chat(base: str, key: str, conv: Conversation, message: str,
                        callback: Callable[[str, dict[str, Any]], None], timeout: float = 1800,
                        opener: Callable[..., Any] = urllib.request.urlopen) -> bool:
    """Run one turn in the conversation's own session; call back with (event, payload). True when a
    terminal run event arrived. Closing the stream interrupts the turn (Hermes' contract)."""
    req = urllib.request.Request(f"{base.rstrip('/')}/api/sessions/{conv.session_id}/chat/stream",
                                 data=json.dumps({"message": message}).encode(), method="POST")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    req.add_header("X-Hermes-Session-Key", conv.session_key)
    name, terminal, total = None, False, 0
    with opener(req, timeout=timeout) as response:
        for raw in response:
            total += len(raw)
            if total > 4 * 1024 * 1024:
                raise ValueError("conversation stream too large")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                try:
                    payload = json.loads(line[6:])
                except ValueError:
                    payload = {}
                event = name or (payload.get("event") if isinstance(payload, dict) else None) or ""
                if isinstance(payload, dict):
                    callback(event, payload)
                terminal = terminal or (event.startswith("run.") and event != "run.started")
                name = None
    return terminal
