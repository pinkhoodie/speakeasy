"""A new Discord thread that Hermes opened as private gets the allowed users added (Hermes #95670)."""
from __future__ import annotations

import io
import json
import urllib.error

from speakeasy import threads


class _Resp(io.BytesIO):
    def __init__(self, body=b"", status=200):
        super().__init__(body); self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _opener(thread_type, calls, fail_puts=False):
    def open_(req, timeout=0):
        calls.append((req.get_method(), req.full_url))
        if req.get_method() == "GET":
            return _Resp(json.dumps({"id": "1", "type": thread_type}).encode())
        if fail_puts:
            raise urllib.error.URLError("down")
        return _Resp(status=204)
    return open_


def test_private_thread_adds_every_allowed_user():
    calls = []
    added = threads.ensure_discord_thread_visible("555", token="t", user_ids=["11", "22"],
                                                  opener=_opener(threads.PRIVATE_THREAD, calls))
    assert added == 2
    assert [c for c in calls if c[0] == "PUT"] == [
        ("PUT", f"{threads.DISCORD_API}/channels/555/thread-members/11"),
        ("PUT", f"{threads.DISCORD_API}/channels/555/thread-members/22")]


def test_public_thread_is_left_alone():
    calls = []
    assert threads.ensure_discord_thread_visible("555", token="t", user_ids=["11"],
                                                 opener=_opener(11, calls)) == 0
    assert all(method == "GET" for method, _ in calls)


def test_failures_never_raise_and_nothing_without_token_or_users():
    assert threads.ensure_discord_thread_visible("555", token="t", user_ids=["11"],
                                                 opener=_opener(threads.PRIVATE_THREAD, [], fail_puts=True)) == 0
    never = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network"))
    assert threads.ensure_discord_thread_visible("555", token="", user_ids=["11"], opener=never) == 0
    assert threads.ensure_discord_thread_visible("555", token="t", user_ids=[], opener=never) == 0
    assert threads.ensure_discord_thread_visible("../x", token="t", user_ids=["11"], opener=never) == 0


def test_allowed_users_reads_numeric_ids_only():
    env = {"DISCORD_ALLOWED_USERS": "123, someone ,456 123"}
    assert threads.discord_allowed_user_ids(env) == ["123", "456"]
    assert threads.discord_allowed_user_ids({}) == []


def test_runner_fixes_visibility_after_opening(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(threads, "webhook_base", lambda home: "http://127.0.0.1:1")
    monkeypatch.setattr(threads, "secret", lambda home: "k" * 40)
    monkeypatch.setattr(threads, "open_thread", lambda *a, **k: threads.Opened("r", "777", "discord"))
    monkeypatch.setattr(threads, "ensure_discord_thread_visible",
                        lambda tid, token, user_ids: seen.update(tid=tid, token=token, users=user_ids) or 1)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "42")
    runner = threads.ThreadRunner(tmp_path, lambda: True)
    opened = runner.open("discord:1", message="m", title="t", delivery_id="d")
    assert opened.thread_id == "777" and seen == {"tid": "777", "token": "tok", "users": ["42"]}
