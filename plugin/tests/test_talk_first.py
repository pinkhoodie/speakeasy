"""Ideas get talked through by the voice; work starts only when asked."""
from fakes import wait_for
from speakeasy import router
from test_routing import start_call, tasks


def test_ideas_are_conversation_and_asks_are_work():
    for q in ("What do you think about people just using generative hypnosis",
              "But I could make a sleep app called Nightcap",
              "what about the idea of a weekly plan",
              "I was thinking we could sell it for ten bucks a month"):
        assert router.is_conversation(q), q
    for q in ("Research the top ways people use it", "map competitors first", "Turn on the lamps",
              "could you look into whether I could make a sleep app"):
        assert not router.is_conversation(q), q
    assert router.is_reaction("What, bro") and router.is_reaction("hang on") and not router.is_reaction("hang the art")
    assert router.starts_talk_mode("we're still ideating, so don't build anything, just talk to me")


def _thinking(worker):
    return [c for k, _, c in worker.sent if k == "session.thinking.append"]


def test_an_idea_is_answered_not_delegated(server, service, hermes):
    _, worker = start_call(server, service)
    worker.delegate("call_i", "But I could make a sleep app called Nightcap")
    wait_for(lambda: any("thinking out loud" in c for c in _thinking(worker)))
    assert not tasks(server) and not hermes.calls


def test_talk_mode_holds_work_until_asked_then_lets_it_through(server, service, hermes):
    _, worker = start_call(server, service)
    worker.delegate("call_m", "we're still in the ideation phase, so don't build anything, just talk to me")
    wait_for(lambda: any("just talk" in c for c in _thinking(worker)))
    worker.feed({"type": "session.output_transcript.delta", "delta": "Sure, let's just talk it through.", "start_ms": 1, "end_ms": 2})
    worker.delegate("call_n", "people pay a lot for a personal coach")
    wait_for(lambda: len(_thinking(worker)) >= 2)
    assert not hermes.calls
    worker.feed({"type": "session.output_transcript.delta", "delta": "Right, that's the gap you'd fill.", "start_ms": 3, "end_ms": 4})
    worker.delegate("call_w", "Okay, go ahead and research what people pay for coaching apps")
    wait_for(lambda: hermes.calls)


def test_yes_to_an_offer_is_a_go_ahead(server, service, hermes):
    _, worker = start_call(server, service)
    worker.feed({"type": "session.output_transcript.delta", "delta": "Want me to look up what they charge?", "start_ms": 1, "end_ms": 2})
    worker.delegate("call_y", "Yeah")
    wait_for(lambda: hermes.calls)
    assert "look up what they charge" in hermes.calls[0]["input"]
