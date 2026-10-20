"""One spoken sentence, one task: the voice sometimes hands a sentence off twice (the whole thing,
then a scrap of its tail), and a tapped answer can arrive twice. Neither may start work twice."""
import asyncio
from types import SimpleNamespace

from speakeasy import calls
from speakeasy.calls import SidebandWorker, whole_request


def stub():
    return SimpleNamespace(recent_requests=[])


def test_a_tail_scrap_handed_off_again_is_an_echo():
    s = stub()
    first = "Where are we on that flaky deploy... I filed an issue about the build... the job failing"
    SidebandWorker._remember_request(s, first)
    # The second handoff carries only the tail; whole_request() joins it back onto the sentence.
    context = f"User: {first}\nUser: build job failing"
    second = whole_request(context)
    assert SidebandWorker._echoes_recent(s, second)
    assert SidebandWorker._echoes_recent(s, "the job failing")


def test_new_words_or_short_replies_are_never_echoes():
    s = stub()
    SidebandWorker._remember_request(s, "Turn on the porch light and the kitchen lights")
    assert not SidebandWorker._echoes_recent(s, "Turn off the porch light")      # "off" is new
    assert not SidebandWorker._echoes_recent(s, "and play some jazz in the kitchen")
    assert not SidebandWorker._echoes_recent(s, "the porch")                      # too short to judge
    assert not SidebandWorker._echoes_recent(s, "yes")


def test_echo_window_expires(monkeypatch):
    s = stub()
    SidebandWorker._remember_request(s, "check the shipping status of the blue parcel")
    s.recent_requests = [(w, at - calls.ECHO_WINDOW_S - 1) for w, at in s.recent_requests]
    assert not SidebandWorker._echoes_recent(s, "check the shipping status of the blue parcel")


def test_same_answer_twice_reaches_the_task_once():
    sent = []

    class Worker:
        interaction = SimpleNamespace(lock=__import__("threading").Lock(), runs={"t1": object()})
        delegations: set = set()
        fragments: list = []
        recent_answers: dict = {}

        def touch(self):
            pass

        def call_connected(self):
            return False

        async def follow_up(self, delegation_id, revision, context, part):
            sent.append((delegation_id, part.request))

    w = Worker()
    a = asyncio.run(SidebandWorker.answer(w, "t1", "Fix it now"))
    b = asyncio.run(SidebandWorker.answer(w, "t1", "fix it now."))
    assert a and a == b and len(sent) == 1
    c = asyncio.run(SidebandWorker.answer(w, "t1", "Watch it first"))   # a different answer still goes
    assert c != a and len(sent) == 2
