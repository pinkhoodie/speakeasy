"""Thread tasks orphaned by a hang-up, restart or reload get settled from the thread itself."""
import sqlite3
import time

from speakeasy import recovery
from speakeasy.store import StateStore


def hermes_db(path, sessions):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sessions (id TEXT, source TEXT, thread_id TEXT, started_at REAL, ended_at REAL, title TEXT)")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT)")
    for sid, thread, messages in sessions:
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,NULL,NULL)", (sid, "discord", thread, time.time()))
        for role, content, tools in messages:
            db.execute("INSERT INTO messages (session_id, role, content, tool_calls) VALUES (?,?,?,?)",
                       (sid, role, content, tools))
    db.commit()
    db.close()


def thread_task(store, key, request, age_s=0.0):
    store.reserve_run(key, "call1", key, 1)
    store.progress(key, "request", request)
    store.update_run(key, None, "running")
    if age_s:
        with store._lock, store._db:
            store._db.execute("UPDATE runs SET updated=? WHERE idem_key=?", (time.time() - age_s, key))


def status(store, key):
    return store._db.execute("SELECT status FROM runs WHERE idem_key=?", (key,)).fetchone()[0]


def test_a_finished_thread_settles_the_stuck_task(tmp_path):
    """Live bug: 'How's my league team doing' answered in its thread within a minute, but the call
    had ended, so the task showed 'running' for two days."""
    db = tmp_path / "state.db"
    hermes_db(db, [("s1", "111", [("user", "How's my league team doing", None),
                                  ("assistant", "", '[{"id": "x"}]'), ("tool", "{}", None),
                                  ("assistant", "Voice: How's my league team\n\nYou're 2-0 and in second place.", None),
                                  ("session_meta", "", None)])])
    store = StateStore(tmp_path / "se.sqlite3")
    thread_task(store, "k1", "How's my league team doing")
    store.set_continued("k1", "s1", "a thread in Discord")
    assert recovery.sweep(store, db, lambda: (), {}) == [("k1", "completed")]
    assert status(store, "k1") == "completed"
    work = store.work(idem_key="k1")
    assert "second place" in work["result"]["spoken"] and "Voice:" not in work["result"]["full"]
    assert recovery.sweep(store, db, lambda: (), {}) == []  # settled once


def test_a_thread_known_only_by_id_is_found(tmp_path):
    db = tmp_path / "state.db"
    hermes_db(db, [("s2", "222", [("user", "weather?", None), ("assistant", "Sunny, 72.", None)])])
    store = StateStore(tmp_path / "se.sqlite3")
    thread_task(store, "k2", "weather tomorrow")
    store.set_thread("k2", "discord", "222", "a thread in #main")
    assert recovery.sweep(store, db, lambda: (), {}) == [("k2", "completed")]
    assert store.continued_for("k2")["session_id"] == "s2"
    assert store.continued_for("k2")["thread_id"] == "222"


def test_live_waits_are_left_alone_until_they_are_orphaned(tmp_path):
    db = tmp_path / "state.db"
    hermes_db(db, [("s3", "333", [("assistant", "Done.", None)])])
    store = StateStore(tmp_path / "se.sqlite3")
    thread_task(store, "k3", "x")
    store.set_continued("k3", "s3", "t")
    assert recovery.sweep(store, db, lambda: (), {"k3": 100.0}, mono=200.0) == []
    assert recovery.sweep(store, db, lambda: (), {"k3": 100.0}, mono=100.0 + 36 * 60) == [("k3", "completed")]


def test_tasks_that_never_answer_are_closed_after_a_while(tmp_path):
    db = tmp_path / "state.db"
    hermes_db(db, [("s4", "444", [("user", "still going", None)])])
    store = StateStore(tmp_path / "se.sqlite3")
    thread_task(store, "fresh", "a", age_s=60)
    store.set_thread("fresh", "discord", "999", "t")
    thread_task(store, "no_turn", "b", age_s=31 * 60)
    store.set_thread("no_turn", "discord", "999", "t")
    thread_task(store, "unfinished", "c", age_s=4 * 3600)
    store.set_continued("unfinished", "s4", "t")
    thread_task(store, "unknown", "d", age_s=4 * 3600)  # from before threads were recorded
    out = dict(recovery.sweep(store, db, lambda: (), {}))
    assert out == {"no_turn": "interrupted", "unfinished": "interrupted", "unknown": "interrupted"}
    assert status(store, "fresh") == "running"
    events = [e["text"] for e in store.work(idem_key="no_turn")["events"]]
    assert recovery.NEVER_STARTED in events
    assert recovery.INTERRUPTED_NOTE in [e["text"] for e in store.work(idem_key="unknown")["events"]]


def test_regular_runs_are_not_touched(tmp_path):
    db = tmp_path / "state.db"
    hermes_db(db, [])
    store = StateStore(tmp_path / "se.sqlite3")
    store.reserve_run("k5", "call1", "k5", 1)
    store.progress("k5", "request", "x")
    store.update_run("k5", "run_abc", "running")  # a Hermes API run: its own stream settles it
    with store._lock, store._db:
        store._db.execute("UPDATE runs SET updated=? WHERE idem_key='k5'", (time.time() - 5 * 3600,))
    assert recovery.sweep(store, db, lambda: (), {}) == []
