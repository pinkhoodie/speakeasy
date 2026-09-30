"""Tune from my calls: call log, proposal checks, applying accepted edits."""
import json
import stat

import pytest

from speakeasy import brief as B
from speakeasy import tune as T

BRIEF = """## User
- Name: Sam. Address him as "you."
- Time zone: US Eastern.

## Assistant persona
- I'm Nova, Sam's assistant. I speak in the first person.

## Capability map
- I can check your calendar and add events.
- I can work directly on your Mac: files, apps and the terminal.

## Answer preferences
- Keep it short.
- Give long detailed answers.

## Current context
- The kitchen remodel.
"""


class FakeBrief:
    def __init__(self, text=BRIEF):
        self._text = text

    def text(self):
        return self._text

    def put(self, text):
        self._text = B.validate(text)
        return {"brief": self._text, "edited": True}

    def get(self):
        return {"brief": self._text}


def test_call_log_is_private_and_pruned(tmp_path):
    now = [1_000_000.0]
    log = T.CallLog(tmp_path, clock=lambda: now[0])
    log.record("old", [{"role": "user", "text": "hi"}], [])
    now[0] += 20 * 86400
    log.record("new", [{"role": "user", "text": "turn on the lights"}], [{"request": "x", "status": "completed"}], "iPhone")
    assert stat.S_IMODE((tmp_path / T.LOG_FILE).stat().st_mode) == 0o600
    assert [c["id"] for c in log.recent()] == ["new"]
    assert log.recent()[0]["device"] == "iPhone"


def test_digest_scrubs_secrets_and_labels_speakers():
    calls = [{"id": "a", "ended": 0, "turns": [{"role": "user", "text": "my key is sk-abcdefghijklmnopqrstuvwxyz123456"},
                                              {"role": "assistant", "text": "I don't have access to your passwords."}],
              "tasks": [{"request": "sign in to the bank", "status": "failed", "result": "No saved login"}]}]
    text = T.digest(calls, "Nova", "Sam")
    assert "sk-abcdef" not in text and "[redacted]" in text
    assert "Nova: I don't have access" in text and "Sam: my key" in text
    assert 'asked "sign in to the bank" -> failed: No saved login' in text


def test_calls_known_only_from_tasks_are_merged():
    logged = [{"id": "a", "ended": 5, "turns": [{"role": "user", "text": "x"}], "tasks": []}]
    tasks = [{"id": "a", "ended": 5, "turns": [], "tasks": []}, {"id": "b", "ended": 1, "turns": [], "tasks": []}]
    assert [c["id"] for c in T.merge_calls(logged, tasks)] == ["b", "a"]


def proposal(**extra):
    base = {"summary": "It kept saying it couldn't reach your passwords.",
            "edits": [
                {"kind": "add", "section": "Capability map", "new": "I can use your saved 1Password logins to sign in to things.",
                 "why": "Said it had no passwords", "evidence": "'I don't know your passwords', Tue"},
                {"kind": "remove", "old": "Give long detailed answers.", "why": "Contradicts keep it short", "evidence": "Tue"},
                {"kind": "change", "old": "- Time zone: US Eastern.", "new": "Time zone: US Pacific.",
                 "why": "Gave times in Eastern", "evidence": "Wed"},
                {"kind": "change", "old": "A line that isn't there", "new": "x", "why": "bad"},
                {"kind": "add", "section": "Nowhere", "new": "x", "why": "bad section"},
                {"kind": "add", "section": "User", "new": "Password: hunter2", "why": "secret"},
                {"kind": "add", "section": "User", "new": "no reason"},
            ],
            "product_issues": [{"what": "Status questions were slow", "evidence": "8 minutes, Tue"}]}
    base.update(extra)
    return "Here you go:\n" + json.dumps(base) + "\nDONE: tuned"


def test_proposal_keeps_only_edits_that_fit_the_brief():
    p = T.parse_proposal(proposal(), BRIEF)
    assert [e["kind"] for e in p["edits"]] == ["add", "remove", "change"]
    assert [e["id"] for e in p["edits"]] == ["e1", "e2", "e3"]
    assert p["product_issues"][0]["what"] == "Status questions were slow"
    with pytest.raises(B.BriefInvalid):
        T.parse_proposal("no json here", BRIEF)


def test_applying_accepted_edits_only():
    edits = T.parse_proposal(proposal(), BRIEF)["edits"]
    out = T.apply_edits(BRIEF, [e for e in edits if e["id"] in {"e1", "e2", "e3"}])
    assert "Give long detailed answers" not in out
    assert "- Time zone: US Pacific." in out and "US Eastern" not in out
    cap = out.split("## Capability map")[1].split("## Answer preferences")[0]
    assert "- I can use your saved 1Password logins to sign in to things." in cap
    assert out.index("1Password") > out.index("terminal")  # added at the end of its section
    B.validate(out)
    assert T.apply_edits(BRIEF, []) == BRIEF


def test_manager_round_trip(tmp_path):
    brief = FakeBrief()
    prompts = []

    def run(prompt, idem):
        prompts.append(prompt)
        return "completed", proposal()

    calls = [{"id": "a", "ended": 1, "turns": [{"role": "user", "text": "You don't, but Hermes does"}], "tasks": []}]
    mgr = T.TuneManager(tmp_path, run, brief, lambda: calls, names=lambda: ("Nova", "Sam"))
    assert mgr.get()["state"] == "none" and mgr.get()["calls"] == 1
    state = mgr.start(background=False)
    assert state["state"] == "ready" and len(state["edits"]) == 3
    assert "Sam: You don't, but Hermes does" in prompts[0] and "## Capability map" in prompts[0]
    done = mgr.apply(["e1"])
    assert done["applied"] == 1 and "1Password" in brief.text() and "Give long detailed answers" in brief.text()
    assert mgr.get()["state"] == "none"
    with pytest.raises(B.BriefInvalid):
        mgr.apply(["e1"])


def test_manager_refuses_without_calls_and_records_failures(tmp_path):
    mgr = T.TuneManager(tmp_path, lambda p, i: ("completed", "{}"), FakeBrief(), lambda: [])
    with pytest.raises(B.BriefInvalid):
        mgr.start(background=False)
    bad = T.TuneManager(tmp_path, lambda p, i: ("failed", ""), FakeBrief(), lambda: [{"id": "a", "ended": 1}])
    assert bad.start(background=False)["state"] == "failed"
