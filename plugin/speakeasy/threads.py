"""Run a voice task in a NEW chat thread through Hermes' own webhook platform.

Hermes' webhook platform can open a fresh thread in a chat and run a message there as if the user
had typed it (route keys ``source_platform`` + ``source_new_thread``; supported where the chat
adapter implements ``create_handoff_thread``: Discord, Telegram topics, Slack, Matrix). The thread
is then an ordinary conversation: the user can follow up in it, and its session is recorded under
the chat platform (not ``webhook``), so thread continuity picks it up later.

Speakeasy writes one route per opted-in channel that has ``new_thread`` on into the profile's
``webhook_subscriptions.json`` (only keys starting ``speakeasy-``; everything else is kept) and
signs each request with a secret kept in its own state dir (0600). Hermes hot-reloads that file,
so no restart is needed once the webhook platform itself is enabled.
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROUTE_PREFIX = "speakeasy-"
EVENT = "speakeasy.task"
THREAD_PLATFORMS = {"discord", "telegram", "slack", "matrix"}
SUBSCRIPTIONS = "webhook_subscriptions.json"
DEFAULT_WEBHOOK_PORT = 8644
SAFE_ID_RE = re.compile(r"^-?[A-Za-z0-9_.:@!#+-]{1,128}$")


class ThreadError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Opened:
    route: str
    thread_id: str
    platform: str


def route_name(target: str) -> str:
    return ROUTE_PREFIX + hashlib.sha256(target.encode()).hexdigest()[:12]


def secret(hermes_home: Path) -> str:
    """The signing secret shared by Speakeasy's routes; created once, private to this user."""
    path = Path(hermes_home) / "speakeasy" / "webhook_secret"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    os.chmod(path, 0o600)
    return value


# -- capability -----------------------------------------------------------------------------------

def _webhook_block(hermes_home: Path) -> dict[str, Any] | None:
    from .delivery import _config_platform_blocks, _hermes_config
    block = _config_platform_blocks(_hermes_config(Path(hermes_home))).get("webhook")
    return block if isinstance(block, dict) and block.get("enabled") else None


def webhook_base(hermes_home: Path) -> str | None:
    """Where this profile's webhook platform listens, or None when it is not enabled."""
    block = _webhook_block(hermes_home)
    if block is None:
        return None
    extra = block.get("extra") if isinstance(block.get("extra"), dict) else {}
    host = str(extra.get("host") or "").strip()
    if not host or host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = int(extra.get("port") or DEFAULT_WEBHOOK_PORT)
    except (TypeError, ValueError):
        return None
    return f"http://{host}:{port}"


def capability(hermes_home: Path, source_supported: bool) -> dict[str, Any]:
    """Can this Hermes open a thread per task? ``source_supported`` = the installed webhook
    platform knows ``source_new_thread``."""
    if not source_supported:
        return {"supported": False, "reason": "This Hermes version can't open threads from Speakeasy yet."}
    if webhook_base(hermes_home) is None:
        return {"supported": False,
                "reason": "Turn on Hermes' webhook platform (platforms.webhook.enabled in config.yaml) to open threads."}
    return {"supported": True, "reason": ""}


def platform_threads(target: str) -> bool:
    return target.split(":", 1)[0] in THREAD_PLATFORMS and ":" in target


# -- route generation -------------------------------------------------------------------------

def _owner(state_db: Path, platform: str, chat_id: str) -> tuple[str, str, str] | None:
    """(user_id, user_name, chat_type) of the person who talks to Hermes in this chat, from the
    newest session there (else on this platform). Read-only."""
    try:
        db = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        rows = db.execute("SELECT origin_json FROM sessions WHERE source=? AND origin_json IS NOT NULL "
                          "ORDER BY started_at DESC LIMIT 200", (platform,)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        db.close()
    fallback = None
    for (raw,) in rows:
        try:
            origin = json.loads(raw or "{}")
        except ValueError:
            continue
        user = str(origin.get("user_id") or "") if isinstance(origin, dict) else ""
        if not user or not SAFE_ID_RE.fullmatch(user):
            continue
        name = str(origin.get("user_name") or "")[:60]
        if str(origin.get("chat_id") or "") == chat_id and origin.get("chat_type") != "thread":
            return user, name, str(origin.get("chat_type") or "group")
        if str(origin.get("parent_chat_id") or "") == chat_id:
            return user, name, "group"
        fallback = fallback or (user, name, "group")
    return fallback


def route_for(target: str, label: str, owner: tuple[str, str, str], key: str) -> dict[str, Any]:
    platform, chat = target.split(":", 1)
    chat_id = chat.split(":", 1)[0] if platform == "telegram" else chat
    user_id, user_name, chat_type = owner
    return {
        "description": f"Speakeasy: voice tasks in new threads in {label}",
        "enabled": True, "events": [EVENT], "secret": key, "prompt": "{message}", "deliver": "log",
        "source_platform": platform, "source_chat_id": chat_id, "source_chat_name": label,
        "source_chat_type": chat_type if chat_type != "thread" else "group",
        "source_user_id": user_id, "source_user_name": user_name or "voice",
        "source_new_thread": True, "source_thread_name": "{title}",
    }


def sync_routes(hermes_home: Path, channels: list[dict[str, Any]], *, supported: bool) -> dict[str, str]:
    """Make ``webhook_subscriptions.json`` hold exactly one Speakeasy route per channel that wants
    a new thread. Returns {target: route_name} for the routes that exist afterwards."""
    hermes_home = Path(hermes_home)
    path = hermes_home / SUBSCRIPTIONS
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        current = current if isinstance(current, dict) else {}
    except (OSError, ValueError):
        current = {}
    wanted: dict[str, dict[str, Any]] = {}
    made: dict[str, str] = {}
    if supported:
        key = secret(hermes_home)
        for channel in channels:
            target = channel["target"]
            if not channel.get("new_thread") or not platform_threads(target):
                continue
            platform, chat = target.split(":", 1)
            owner = _owner(hermes_home / "state.db", platform, chat.split(":", 1)[0])
            if owner is None:
                continue
            name = route_name(target)
            wanted[name] = route_for(target, channel["label"], owner, key)
            made[target] = name
    kept = {k: v for k, v in current.items() if not str(k).startswith(ROUTE_PREFIX)}
    merged = {**kept, **wanted}
    if merged != current:
        tmp = path.with_suffix(".json.speakeasy-tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2)
        os.replace(tmp, path)
    return made


def route_exists(hermes_home: Path, target: str) -> bool:
    try:
        current = json.loads((Path(hermes_home) / SUBSCRIPTIONS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(current, dict) and route_name(target) in current


# -- opening a thread -----------------------------------------------------------------------------

def open_thread(base: str, key: str, target: str, *, message: str, title: str, delivery_id: str,
                opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 20) -> Opened:
    """Ask Hermes to open a thread for ``target`` and run ``message`` there."""
    route = route_name(target)
    body = json.dumps({"event_type": EVENT, "message": message, "title": title[:90] or "Voice task"}).encode()
    req = urllib.request.Request(f"{base.rstrip('/')}/webhooks/{route}", data=body, method="POST")
    stamp = str(int(time.time()))
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Request-ID", delivery_id)
    req.add_header("X-Webhook-Timestamp", stamp)
    req.add_header("X-Webhook-Signature-V2", hmac.new(key.encode(), stamp.encode() + b"." + body,
                                                      hashlib.sha256).hexdigest())
    try:
        with opener(req, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            reply = json.loads(response.read(20_000) or b"{}")
    except urllib.error.HTTPError as exc:
        raise ThreadError(f"Hermes refused the new thread (HTTP {exc.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ThreadError(f"Hermes webhook unreachable ({type(exc).__name__})") from None
    thread_id = str(reply.get("thread_id") or "") if isinstance(reply, dict) else ""
    if status != 202 or reply.get("status") != "accepted" or not SAFE_ID_RE.fullmatch(thread_id):
        raise ThreadError("Hermes did not confirm a new thread")
    return Opened(route, thread_id, target.split(":", 1)[0])


# -- following the thread's first answer ---------------------------------------------------------

def thread_session(state_db: Path, platform: str, thread_id: str) -> str | None:
    try:
        db = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        row = db.execute("SELECT id FROM sessions WHERE source=? AND thread_id=? AND ended_at IS NULL "
                         "ORDER BY started_at DESC LIMIT 1", (platform, str(thread_id))).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        db.close()
    return row[0] if row else None


def session_answer(state_db: Path, session_id: str) -> str | None:
    """The finished reply of a session's turn: its newest message is a plain assistant message."""
    try:
        db = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        row = db.execute("SELECT role, content, tool_calls FROM messages WHERE session_id=? "
                         "AND role IN ('user', 'assistant', 'tool') ORDER BY id DESC LIMIT 1",
                         (session_id,)).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        db.close()
    if not row or row[0] != "assistant" or (row[2] and row[2] not in ("[]", "null")) or not (row[1] or "").strip():
        return None
    return row[1]


def session_title(state_db: Path, session_id: str) -> str | None:
    """The title Hermes gave the session (it names new threads after the first turn)."""
    try:
        db = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        row = db.execute("SELECT title FROM sessions WHERE id=?", (session_id,)).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        db.close()
    title = " ".join(str(row[0] or "").split()) if row else ""
    return title[:80] or None


def wait_for_answer(state_db: Path, opened: Opened, timeout_s: float, poll_s: float = 3.0,
                    sleep: Callable[[float], None] = time.sleep,
                    on_session: Callable[[str], None] | None = None,
                    on_title: Callable[[str], None] | None = None) -> str | None:
    """Poll until the thread's first turn finishes; None on timeout (the answer still lands in the
    thread, only the call doesn't hear it)."""
    waited, session_id, title = 0.0, None, None
    while waited <= timeout_s:
        if session_id is None:
            session_id = thread_session(state_db, opened.platform, opened.thread_id)
            if session_id and on_session:
                on_session(session_id)
        if session_id:
            if on_title:
                named = session_title(state_db, session_id)
                if named and named != title:
                    title = named
                    on_title(named)
            answer = session_answer(state_db, session_id)
            if answer is not None:
                return answer
        sleep(poll_s)
        waited += poll_s
    return None


class ThreadRunner:
    """What the call needs to run a task in a new thread (tests pass a fake with the same shape)."""

    def __init__(self, hermes_home: Path, source_supported: Callable[[], bool], answer_timeout_s: float = 1800):
        self.home, self.source_supported, self.answer_timeout_s = Path(hermes_home), source_supported, answer_timeout_s

    def available(self, target: str) -> bool:
        return (platform_threads(target) and route_exists(self.home, target)
                and capability(self.home, self.source_supported())["supported"])

    def open(self, target: str, *, message: str, title: str, delivery_id: str) -> Opened:
        base = webhook_base(self.home)
        if base is None:
            raise ThreadError("Hermes' webhook platform is off")
        return open_thread(base, secret(self.home), target, message=message, title=title, delivery_id=delivery_id)

    def wait(self, opened: Opened, on_session: Callable[[str], None],
             on_title: Callable[[str], None] | None = None) -> str | None:
        return wait_for_answer(self.home / "state.db", opened, self.answer_timeout_s,
                               on_session=on_session, on_title=on_title)
