"""A task that needs a decision asks it as a card: numbered answers, read aloud, answered by tap or voice."""
from speakeasy import text, views
from speakeasy.prompt import builder as P


ASK = """Voice: plan the garden beds
I sketched two bed layouts and priced the soil.

```speakeasy-question
{"question": "Which layout should I order soil for?", "options": ["Four raised beds", "One long bed"], "recommended": 1}
```
DONE: garden
SPOKEN: I sketched two layouts and priced the soil."""


def test_a_question_becomes_a_card_and_is_said_last():
    r = text.split_result(ASK, ())
    q = r["question"]
    assert q["kind"] == "question" and q["options"] == ["Four raised beds", "One long bed"] and q["recommended"] == 1
    assert r["views"][0] == q
    assert r["spoken"].startswith("I sketched two layouts")
    assert "which layout should I order soil for? Four raised beds or One long bed." in r["spoken"]
    assert r["spoken"].endswith("I'd go with one long bed.")
    assert "speakeasy-question" not in r["full"]
    assert "1. Four raised beds" in r["full"] and "2. One long bed (recommended)" in r["full"]


def test_bad_questions_are_dropped_quietly():
    for block in ('{"question": "Go?", "options": ["Yes"]}', "not json", '{"options": ["a", "b"]}'):
        out, q = views.extract_question(f"Done.\n```speakeasy-question\n{block}\n```")
        assert q is None and "speakeasy-question" not in out
    out, q = views.extract_question('x\n```speakeasy-question\n{"question": "Pick", "options": ["a", "b"], "recommended": 9}\n```')
    assert q and "recommended" not in q


def test_every_task_prompt_explains_how_to_ask():
    names = P.Names("Ava", "Sam") if hasattr(P, "Names") else None
    if names is None:
        return
    for msg in (P.continuation_message(names, "x", "x"), P.thread_task_message(names, "x", "x", "")):
        assert "speakeasy-question" in msg


def test_tapping_an_answer_continues_the_same_task(server, service, hermes):
    from fakes import http, wait_for
    from test_routing import start_call, tasks
    hermes.responder = lambda prompt, sid: (ASK if "garden" in prompt.lower() and "One long bed" not in prompt
                                            else "Ordered.\nDONE: soil\nSPOKEN: Soil is ordered for the long bed.")
    session, worker = start_call(server, service)
    worker.delegate("call_q", "Plan the garden beds for spring")
    done = wait_for(lambda: [t for t in tasks(server) if t["status"] == "completed"])[0]
    assert done["result"]["question"]["options"] == ["Four raised beds", "One long bed"]
    assert any("I'd go with one long bed" in c for k, _, c in worker.sent if k == "session.commentary.append") \
        or "I'd go with one long bed" in done["result"]["spoken"]
    status, body = http(server.base_url, "POST", f"/voice/interactions/{session['interaction_id']}/answer",
                        {"task_id": done["task_id"], "text": "One long bed"}, server.token)
    assert status == 200 and body["sent"], body
    wait_for(lambda: len(hermes.calls) >= 2)
    second = hermes.calls[1]
    assert "One long bed" in second["input"] and "garden" in second["input"].lower()   # same task, with context
    assert http(server.base_url, "POST", f"/voice/interactions/{session['interaction_id']}/answer",
                {"task_id": "nope", "text": "x"}, server.token)[0] == 404
