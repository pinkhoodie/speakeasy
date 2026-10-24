"""Answering a waiting approval by voice: once, deny, session only when offered, never always."""
from __future__ import annotations

import pytest

from fakes import http, wait_for
from speakeasy import calls, router
from speakeasy.hermes_api import HermesAPI, HermesError
from speakeasy.prompt import builder as P
from test_routing import start_call


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(calls, "SETTLE_S", 0.2)


def say(worker, delegation_id, words, **extra):
    worker.feed({"type": "session.output_transcript.delta", "delta": "Should I allow it?", "start_ms": 1, "end_ms": 2})
    worker.delegate(delegation_id, words, **extra)


def spoken(worker):
    return [c for k, _, c in worker.sent if k == "session.commentary.append"]


def _waiting(server, service, hermes, n=1):
    hermes.approval_first = True
    session, worker = start_call(server, service)
    for i in range(n):
        say(worker, f"call_risky{i}", ["Clean up my downloads folder", "Delete the old build logs"][i])
    wait_for(lambda: len(worker.pending_approvals()) == n)
    return session, worker


@pytest.mark.parametrize("said,choice", [
    ("just this once", "once"), ("yes once", "once"), ("go ahead just this time", "once"), ("yeah", "yes"),
    ("no", "deny"), ("nope, don't do it", "deny"), ("for the rest of this task", "session"),
    ("yes for this session", "session"), ("yes, always", "always"), ("don't ask me again", "always"),
    ("allow it permanently", "always"),
])
def test_short_answers(said, choice):
    assert router.approval_answer(said) == choice


@pytest.mark.parametrize("said", ["what's the weather", "no, find me a dentist instead", "can you check the score",
                                  "book a table for two at eight tonight please and also text Dana"])
def test_other_words_are_not_answers(said):
    assert router.approval_answer(said) is None


def test_the_client_never_sends_always_and_session_only_when_offered():
    client = HermesAPI.__new__(HermesAPI)
    with pytest.raises(HermesError):
        client.approve("run_1", "apr_1", "always", ["once", "session", "always", "deny"])
    with pytest.raises(HermesError):
        client.approve("run_1", "apr_1", "session", ["once", "deny"])


def test_just_this_once_is_confirmed_by_the_server_before_it_is_said(server, service, hermes):
    _, worker = _waiting(server, service, hermes)
    question = wait_for(lambda: [c for c in spoken(worker) if "needs your OK first" in c])[0]
    assert "Delete an old file" in question and "just this once, or not" in question
    assert "always" not in question.lower() and "permanent" not in question.lower()
    say(worker, "call_ok", "just this once")
    wait_for(lambda: hermes.approvals)
    assert hermes.approvals[-1]["choice"] == "once"
    wait_for(lambda: "Done, allowed just this once." in spoken(worker))
    assert len(hermes.calls) == 1                                          # the answer started no new task


def test_no_declines(server, service, hermes):
    _, worker = _waiting(server, service, hermes)
    say(worker, "call_no", "no")
    wait_for(lambda: hermes.approvals and hermes.approvals[-1]["choice"] == "deny")
    wait_for(lambda: any("declined" in c for c in spoken(worker)))


def test_always_is_refused_and_nothing_is_sent(server, service, hermes):
    _, worker = _waiting(server, service, hermes)
    say(worker, "call_always", "yes, always")
    wait_for(lambda: any("can't allow that permanently" in c for c in spoken(worker)))
    assert hermes.approvals == []


def test_session_only_when_hermes_offered_it(server, service, hermes):
    _, worker = _waiting(server, service, hermes)
    say(worker, "call_sess", "for the rest of this task")
    wait_for(lambda: P.SESSION_NOT_OFFERED_SPOKEN in spoken(worker))
    assert hermes.approvals == []


def test_session_when_offered(server, service, hermes):
    hermes.approval_choices = ("once", "session", "always", "deny")
    session, worker = _waiting(server, service, hermes)
    question = wait_for(lambda: [c for c in spoken(worker) if "needs your OK first" in c])[0]
    assert "for the rest of this task" in question and "always" not in question.lower()
    snap = http(server.base_url, "GET", f"/voice/interactions/{session['interaction_id']}", token=server.token)[1]
    assert snap["approval"]["choices"] == ["once", "session", "deny"]          # never "always", even offered
    say(worker, "call_sess", "yes for the rest of this task")
    wait_for(lambda: hermes.approvals and hermes.approvals[-1]["choice"] == "session")
    wait_for(lambda: "Done, it's allowed for the rest of this task." in spoken(worker))


def test_a_refused_answer_is_never_claimed(server, service, hermes):
    _, worker = _waiting(server, service, hermes)
    hermes.approval_choices = ()  # Hermes refuses every answer now
    say(worker, "call_ok", "just this once")
    wait_for(lambda: any("didn't go through" in c for c in spoken(worker)))
    assert not any(c.startswith("Done") for c in spoken(worker))
    assert len(worker.pending_approvals()) == 1                               # still waiting, untouched


def test_yeah_with_two_waiting_asks_which(server, service, hermes):
    _, worker = _waiting(server, service, hermes, n=2)
    say(worker, "call_yeah", "yeah")
    wait_for(lambda: any("Which one do you mean?" in c for c in spoken(worker)))
    assert hermes.approvals == []


def test_the_card_route_takes_session_only_when_offered(server, service, hermes):
    session, worker = _waiting(server, service, hermes)
    iid = session["interaction_id"]
    approval = http(server.base_url, "GET", f"/voice/interactions/{iid}", token=server.token)[1]["approval"]
    base = {"run_id": approval["run_id"], "request_id": approval["request_id"]}
    url = f"/voice/interactions/{iid}/approval"
    assert http(server.base_url, "POST", url, {**base, "choice": "always"}, server.token)[0] == 400
    assert http(server.base_url, "POST", url, {**base, "choice": "session"}, server.token)[0] == 400
    assert hermes.approvals == []
