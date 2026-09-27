"""Durable call/task state (sqlite under ``<HERMES_HOME>/speakeasy/``).

Admission and run receipts make retries non-duplicating; work events and results feed the task
list; notices are deduplicated across restarts; email drafts carry their exact-content hash.
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .emails import canonical_json, draft_sha256
from .text import (AUTHORED_PRECEDENCE_S, SETTLED, TERMINAL, notice_text, public_result, safe_user_text,
                   valid_short_status)

STALE_AFTER_S = 90


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("""CREATE TABLE IF NOT EXISTS admissions (
                request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                interaction_id TEXT NOT NULL, response_json TEXT, created REAL NOT NULL)""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS runs (
                idem_key TEXT PRIMARY KEY, interaction_id TEXT NOT NULL,
                delegation_id TEXT NOT NULL, revision INTEGER NOT NULL,
                run_id TEXT, status TEXT NOT NULL, updated REAL NOT NULL,
                short_status TEXT, detail TEXT, progress_updated REAL, status_source TEXT,
                authored_at REAL, result_json TEXT, settled_at REAL, title TEXT, dismissed INTEGER,
                session_id TEXT, summary TEXT, continued TEXT)""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS work_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, idem_key TEXT NOT NULL,
                kind TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL)""")
            self._db.execute("CREATE INDEX IF NOT EXISTS work_events_key ON work_events(idem_key, seq)")
            self._db.execute("""CREATE TABLE IF NOT EXISTS notices (
                dedupe_key TEXT PRIMARY KEY, created REAL NOT NULL, outcome TEXT)""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS email_drafts (
                draft_id TEXT PRIMARY KEY, idem_key TEXT NOT NULL, session_id TEXT,
                draft_json TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, action_run_id TEXT, error TEXT)""")
            self._db.execute("CREATE INDEX IF NOT EXISTS email_drafts_key ON email_drafts(idem_key, created)")

    # -- session admission -------------------------------------------------------------
    def reserve_session(self, request_id: str, fingerprint: str, interaction_id: str) -> tuple[str, dict[str, Any] | None]:
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT fingerprint,response_json FROM admissions WHERE request_id=?", (request_id,)).fetchone()
            if row:
                if not hmac.compare_digest(row[0], fingerprint):
                    return "conflict", None
                return ("replay", json.loads(row[1])) if row[1] else ("pending", None)
            self._db.execute("INSERT INTO admissions VALUES (?,?,?,?,?)",
                             (request_id, fingerprint, interaction_id, None, time.time()))
            return "created", None

    def complete_session(self, request_id: str, response: dict[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE admissions SET response_json=? WHERE request_id=?",
                             (json.dumps(response, separators=(",", ":")), request_id))

    def fail_session(self, request_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM admissions WHERE request_id=? AND response_json IS NULL", (request_id,))

    # -- runs --------------------------------------------------------------------------
    def reserve_run(self, key: str, interaction_id: str, delegation_id: str, revision: int) -> tuple[str, str | None]:
        with self._lock, self._db:
            row = self._db.execute("SELECT run_id,status FROM runs WHERE idem_key=?", (key,)).fetchone()
            if row:
                return row[1], row[0]
            self._db.execute(
                """INSERT INTO runs (idem_key,interaction_id,delegation_id,revision,run_id,status,updated)
                   VALUES (?,?,?,?,?,?,?)""",
                (key, interaction_id, delegation_id, revision, None, "admitting", time.time()))
            return "created", None

    def set_session(self, key: str, session_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE runs SET session_id=? WHERE idem_key=?", (session_id, key))

    def session_for(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT session_id FROM runs WHERE idem_key=?", (key,)).fetchone()
        return row[0] if row and row[0] else None

    def set_continued(self, key: str, session_id: str, label: str) -> None:
        """This task runs inside an existing Hermes conversation (thread continuity)."""
        with self._lock, self._db:
            self._db.execute("UPDATE runs SET session_id=?, continued=? WHERE idem_key=?",
                             (session_id, json.dumps({"session_id": session_id, "label": label}), key))

    def continued_for(self, key: str) -> dict[str, str] | None:
        with self._lock:
            row = self._db.execute("SELECT continued FROM runs WHERE idem_key=?", (key,)).fetchone()
        try:
            return json.loads(row[0]) if row and row[0] else None
        except ValueError:
            return None

    def key_for_run(self, run_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT idem_key FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return row[0] if row else None

    def title(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT title FROM runs WHERE idem_key=?", (key,)).fetchone()
        return row[0] if row and row[0] else None

    def set_title(self, key: str, title: str | None, summary: str | None = None) -> None:
        with self._lock, self._db:
            if title:
                self._db.execute("UPDATE runs SET title=? WHERE idem_key=?", (title, key))
            if summary:
                self._db.execute("UPDATE runs SET summary=? WHERE idem_key=?", (summary, key))

    def dismiss(self, run_ids: list[str]) -> list[str]:
        """Hide finished tasks from the task list. Running tasks are never dismissed."""
        settled = sorted(TERMINAL | {"rejected"})
        marks = ",".join("?" * len(settled))
        done = []
        with self._lock, self._db:
            for run_id in run_ids:
                cursor = self._db.execute(
                    f"UPDATE runs SET dismissed=1 WHERE run_id=? AND status IN ({marks})", (run_id, *settled))
                if cursor.rowcount:
                    done.append(run_id)
        return done

    def dismiss_key(self, key: str) -> bool:
        settled = sorted(TERMINAL | {"rejected"})
        marks = ",".join("?" * len(settled))
        with self._lock, self._db:
            cursor = self._db.execute(
                f"UPDATE runs SET dismissed=1 WHERE idem_key=? AND status IN ({marks})", (key, *settled))
        return bool(cursor.rowcount)

    def update_run(self, key: str, run_id: str | None, status: str) -> None:
        now = time.time()
        with self._lock, self._db:
            self._db.execute("UPDATE runs SET run_id=COALESCE(?,run_id),status=?,updated=? WHERE idem_key=?",
                             (run_id, status, now, key))
            if status in SETTLED:
                self._db.execute("UPDATE runs SET settled_at=? WHERE idem_key=?", (now, key))

    # -- notices / meta ----------------------------------------------------------------
    def claim_notice(self, dedupe_key: str) -> bool:
        """True exactly once per key, across restarts."""
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO notices (dedupe_key,created) VALUES (?,?)", (dedupe_key, time.time()))
            return cursor.rowcount == 1

    def notice_outcome(self, dedupe_key: str, outcome: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE notices SET outcome=? WHERE dedupe_key=?", (outcome, dedupe_key))

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", (key, value))

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def mark_call_ended(self, at: float | None = None) -> None:
        self.set_meta("last_call_end", repr(at if at is not None else time.time()))

    # -- work events -------------------------------------------------------------------
    def request_text(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT text FROM work_events WHERE idem_key=? AND kind='request' ORDER BY seq LIMIT 1", (key,)).fetchone()
        return row[0] if row else None

    def away(self, limit: int = 3) -> list[dict[str, Any]]:
        """Runs that settled (terminal or approval-needed) since the previous call ended."""
        ended = self.get_meta("last_call_end")
        if ended is None:
            return []
        with self._lock:
            rows = self._db.execute(
                """SELECT idem_key,run_id,status,settled_at,result_json FROM runs
                   WHERE settled_at > ? AND run_id IS NOT NULL ORDER BY settled_at DESC LIMIT ?""",
                (float(ended), limit)).fetchall()
        items = []
        for key, run_id, status, settled_at, result_json in rows:
            if status not in SETTLED:
                continue
            spoken = None
            if result_json:
                try:
                    spoken = json.loads(result_json).get("spoken")
                except ValueError:
                    spoken = None
            items.append({"run_id": run_id, "request": notice_text(self.request_text(key), 140),
                          "status": status, "spoken": notice_text(spoken, 300), "finished_at": settled_at})
        return items

    def progress(self, key: str, kind: str, text: str) -> None:
        """Persist only bounded, user-facing milestones; never tool arguments or reasoning."""
        if kind not in {"request", "milestone", "tool", "result"} or not text.strip():
            return
        text = re.sub(r"\s+", " ", text).strip()[:4000 if kind == "result" else 240]
        with self._lock, self._db:
            if not self._db.execute("SELECT 1 FROM runs WHERE idem_key=?", (key,)).fetchone():
                return
            previous = self._db.execute(
                "SELECT kind,text FROM work_events WHERE idem_key=? ORDER BY seq DESC LIMIT 1", (key,)).fetchone()
            if previous == (kind, text):
                return
            self._db.execute("UPDATE runs SET updated=? WHERE idem_key=?", (time.time(), key))
            self._db.execute("INSERT INTO work_events (idem_key,kind,text,created) VALUES (?,?,?,?)",
                             (key, kind, text, time.time()))
            self._db.execute("""DELETE FROM work_events WHERE idem_key=? AND kind!='request' AND seq NOT IN
                (SELECT seq FROM work_events WHERE idem_key=? ORDER BY seq DESC LIMIT 30)""", (key, key))

    def user_progress(self, key: str, short_status: str, detail: str) -> None:
        """Persist an authored, sanitized display update for one exact run."""
        safe_status = valid_short_status(short_status)
        safe_detail = safe_user_text(detail, 500)
        if not safe_status or not safe_detail:
            return
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                """UPDATE runs SET authored_at=?,updated=? WHERE idem_key=? AND short_status=?
                   AND detail=? AND status_source='authored'""", (now, now, key, safe_status, safe_detail))
            self._db.execute(
                """UPDATE runs SET short_status=?,detail=?,progress_updated=?,updated=?,
                   status_source='authored',authored_at=?
                   WHERE idem_key=? AND run_id IS NOT NULL AND NOT (short_status IS ? AND detail IS ?
                   AND status_source IS 'authored')""",
                (safe_status, safe_detail, now, now, now, key, safe_status, safe_detail))

    def tool_progress(self, key: str, short_status: str, detail: str) -> bool:
        """Persist a tool-derived status unless an authored status is still fresh."""
        safe_status = valid_short_status(short_status)
        safe_detail = safe_user_text(detail, 500)
        if not safe_status or not safe_detail:
            return False
        now = time.time()
        with self._lock, self._db:
            cursor = self._db.execute(
                """UPDATE runs SET short_status=?,detail=?,progress_updated=?,updated=?, status_source='tool'
                   WHERE idem_key=? AND run_id IS NOT NULL AND (authored_at IS NULL OR authored_at <= ?)
                   AND NOT (short_status IS ? AND detail IS ? AND status_source IS 'tool')""",
                (safe_status, safe_detail, now, now, key, now - AUTHORED_PRECEDENCE_S, safe_status, safe_detail))
            return cursor.rowcount > 0

    def set_result(self, key: str, result: dict[str, Any] | None) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE runs SET result_json=? WHERE idem_key=?",
                             (json.dumps(result) if result else None, key))

    def result_cards(self, run_id: str) -> list[Any]:
        """Stored cards for one exact finished run, including server-side image paths."""
        with self._lock:
            row = self._db.execute("SELECT status,result_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row[0] not in TERMINAL or not row[1]:
            return []
        try:
            cards = (json.loads(row[1]) or {}).get("cards")
        except (ValueError, AttributeError):
            return []
        return cards if isinstance(cards, list) else []

    def latest_tasks(self, limit: int = 12) -> list[dict[str, Any]]:
        """Every task of the most recent call (oldest first), for the after-call Work view."""
        with self._lock:
            row = self._db.execute("SELECT interaction_id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
            if row is None or not row[0]:
                return []
            rows = self._db.execute(
                "SELECT idem_key, delegation_id FROM runs WHERE interaction_id=? AND status!='rejected' ORDER BY rowid",
                (row[0],)).fetchall()
        tasks = []
        for key, delegation_id in rows[-limit:]:
            work = self.work(idem_key=key)
            if work is not None and not work.get("dismissed"):
                work["task_id"] = delegation_id or key
                tasks.append(work)
        return tasks

    def work(self, run_id: str | None = None, idem_key: str | None = None,
             assistant_name: str = "Hermes") -> dict[str, Any] | None:
        """The latest admitted job or one exact server-owned run, including past calls."""
        cols = """idem_key,run_id,status,updated,short_status,detail,progress_updated,
                  status_source,result_json,title,dismissed,summary,continued"""
        with self._lock:
            if idem_key:
                row = self._db.execute(f"SELECT {cols} FROM runs WHERE idem_key=?", (idem_key,)).fetchone()
            elif run_id:
                row = self._db.execute(f"SELECT {cols} FROM runs WHERE run_id=?", (run_id,)).fetchone()
            else:
                row = self._db.execute(f"SELECT {cols} FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
            if row is None:
                return None
            (key, actual_run_id, status, updated, short_status, detail, progress_updated,
             status_source, result_json, title, dismissed, summary, continued) = row
            events = self._db.execute(
                "SELECT kind,text,created FROM work_events WHERE idem_key=? ORDER BY seq DESC LIMIT 30", (key,)).fetchall()
        stale = status not in TERMINAL | {"waiting_for_approval"} and time.time() - updated > STALE_AFTER_S
        display_status, display_detail, display_updated = short_status, detail, progress_updated
        source = (status_source or "authored") if short_status else None
        if stale:
            display_status = "Status unconfirmed"
            display_detail = detail or "No verified update has arrived recently."
            display_updated, source = updated, "system"
        elif status == "waiting_for_approval":
            display_status = "Needs your approval"
            display_detail = detail or f"{assistant_name} is waiting for an explicit approval decision."
            display_updated, source = updated, "system"
        result = None
        if status in TERMINAL and result_json:
            try:
                result = json.loads(result_json)
            except ValueError:
                result = None
        work = {
            "run_id": actual_run_id, "status": status, "stale": stale, "updated": updated,
            "events": [{"kind": kind, "text": text, "at": created} for kind, text, created in reversed(events)],
            "short_status": display_status, "detail": display_detail,
            "source_run_id": actual_run_id, "updated_at": display_updated,
            "status_source": source, "result": public_result(result),
            "title": title, "summary": summary, "dismissed": bool(dismissed),
            "email_drafts": self.drafts_for(key),
        }
        if continued:
            try:
                work["continued_in"] = json.loads(continued).get("label")
            except ValueError:
                pass
        return work

    # -- email drafts ------------------------------------------------------------------
    def add_draft(self, key: str, session_id: str | None, draft: dict[str, Any]) -> dict[str, Any]:
        """Store one draft for a task. Older pending/revising drafts of that task (or of the same
        Hermes session, e.g. a spoken "revise it" follow-up) are superseded and can no longer be
        approved."""
        now = time.time()
        draft_id = "ed_" + secrets.token_hex(12)
        body, digest = canonical_json(draft), draft_sha256(draft)
        with self._lock, self._db:
            self._db.execute(
                "UPDATE email_drafts SET status='superseded', updated=? WHERE status IN ('pending','revising') "
                "AND (idem_key=? OR (? IS NOT NULL AND session_id=?))", (now, key, session_id, session_id))
            self._db.execute("INSERT INTO email_drafts VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (draft_id, key, session_id, body, digest, "pending", now, now, None, None))
        return self.draft(draft_id) or {}

    def draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT draft_id,idem_key,session_id,draft_json,sha256,status,created,updated,action_run_id,error "
                "FROM email_drafts WHERE draft_id=?", (draft_id,)).fetchone()
        return self._draft_row(row) if row else None

    @staticmethod
    def _draft_row(row: tuple) -> dict[str, Any]:
        draft_id, key, session_id, body, digest, status, created, updated, action_run_id, error = row
        out = {"draft_id": draft_id, **json.loads(body), "sha256": digest, "status": status,
               "created_at": created, "updated_at": updated}
        if error:
            out["error"] = error
        out["_key"], out["_session_id"], out["_action_run_id"] = key, session_id, action_run_id
        return out

    def drafts_for(self, key: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT draft_id,idem_key,session_id,draft_json,sha256,status,created,updated,action_run_id,error "
                "FROM email_drafts WHERE idem_key=? AND status!='superseded' ORDER BY created", (key,)).fetchall()
        return [{k: v for k, v in self._draft_row(r).items() if not k.startswith("_")} for r in rows]

    def transition_draft(self, draft_id: str, expected: set[str], status: str, *,
                         action_run_id: str | None = None, error: str | None = None) -> bool:
        """Compare-and-set a draft's status; False when it was not in an expected state."""
        marks = ",".join("?" * len(expected))
        with self._lock, self._db:
            cursor = self._db.execute(
                f"UPDATE email_drafts SET status=?, updated=?, action_run_id=COALESCE(?,action_run_id), error=? "
                f"WHERE draft_id=? AND status IN ({marks})",
                (status, time.time(), action_run_id, error, draft_id, *sorted(expected)))
        return cursor.rowcount == 1

    def draft_for_action_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT draft_id,idem_key,session_id,draft_json,sha256,status,created,updated,action_run_id,error "
                "FROM email_drafts WHERE action_run_id=?", (run_id,)).fetchone()
        return self._draft_row(row) if row else None

    def close(self) -> None:
        with self._lock:
            self._db.close()
