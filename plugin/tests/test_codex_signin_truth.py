"""Settings said "signed in to ChatGPT" but the call failed with "not signed in": `codex login status`
only reads the saved file, so a revoked or expired sign-in (or a dead API key) still reads as logged in.
A call that OpenAI rejects must flip Settings to not-signed-in until the user signs in again."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from speakeasy import codex_transport as ct


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex"; home.mkdir()
    (home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}))
    monkeypatch.setenv("CODEX_HOME", str(home))
    ct._LOGIN_CACHE.clear(); ct._REJECTED.clear()
    yield home
    ct._LOGIN_CACHE.clear(); ct._REJECTED.clear()


def status_says(text: str):
    return lambda *a, **k: SimpleNamespace(returncode=0, stdout=text, stderr="")


def test_a_rejected_call_flips_settings_to_not_signed_in(codex_home, tmp_path):
    binary = tmp_path / "codex-bin"
    runner = status_says("Logged in using ChatGPT")
    assert ct.login_status(binary, runner=runner)[0] is True
    ct.mark_signed_out(binary, ct.explain_codex_error("unexpected status 401 Unauthorized: token revoked"))
    ok, message = ct.login_status(binary, runner=runner)
    assert ok is False and "codex login" in message
    # Signing in again rewrites auth.json: Settings trusts the status check again.
    time.sleep(0.01)
    (codex_home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "fresh": True}))
    os.utime(codex_home / "auth.json", (time.time() + 5, time.time() + 5))
    assert ct.login_status(binary, runner=runner)[0] is True


def test_the_error_names_the_real_problem():
    missing = ct.explain_codex_error("401 Unauthorized: You didn't provide an API key.")
    rejected = ct.explain_codex_error("unexpected status 401 Unauthorized: Incorrect API key provided: sk-proj-***")
    assert "isn't signed in" in missing
    assert "rejected" in rejected and "codex login" in rejected


def test_an_api_key_login_is_named_as_such(codex_home, tmp_path):
    ok, message = ct.login_status(tmp_path / "b", runner=status_says("Logged in using an API key - sk-proj-***0000"))
    assert ok is True and "API key" in message and "ChatGPT" in message


def test_the_logged_reason_never_carries_a_key():
    assert "abcdef123456" not in ct._redacted("Incorrect API key provided: sk-proj-abcdef123456")


def test_the_transport_marks_the_sign_in_bad_when_openai_rejects_it(codex_home, tmp_path):
    binary = tmp_path / "codex-bin"
    t = ct.CodexTransport.__new__(ct.CodexTransport); t.binary = binary
    with pytest.raises(ct.CodexStartError):
        t._fail("unexpected status 401 Unauthorized: token has been revoked")
    assert ct.login_status(binary, runner=status_says("Logged in using ChatGPT"))[0] is False
    # A voice-choice failure is not a sign-in problem.
    ct._REJECTED.clear()
    with pytest.raises(ct.CodexStartError):
        t._fail("realtime voice `marin` is not supported for v3")
    assert ct.login_status(binary, runner=status_says("Logged in using ChatGPT"))[0] is True
