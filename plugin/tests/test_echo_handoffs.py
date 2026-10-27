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

    async def run(text):
        out = await SidebandWorker.answer(w, "t1", text)
        await asyncio.sleep(0)   # the follow-up runs in the background
        return out

    a = asyncio.run(run("Fix it now"))
    b = asyncio.run(run("fix it now."))
    assert a and a == b and len(sent) == 1
    w.recent_answers = {k: (d, at - calls.ECHO_WINDOW_S - 60) for k, (d, at) in w.recent_answers.items()}
    assert asyncio.run(run("Fix it now")) == a and len(sent) == 1   # a retap much later: still once
    c = asyncio.run(run("Watch it first"))   # a different answer still goes
    assert c != a and len(sent) == 2


def test_a_slow_follow_up_never_holds_the_tap():
    """Seen live: a task in a Discord thread took over 15 s to follow up, the tap timed out, and the
    app put the question back up although the answer was already on its way."""
    class Worker:
        interaction = SimpleNamespace(lock=__import__("threading").Lock(), runs={"t1": object()})
        delegations: set = set()
        fragments: list = []
        recent_answers: dict = {}
        done: list = []

        def touch(self):
            pass

        def call_connected(self):
            return False

        async def follow_up(self, delegation_id, revision, context, part):
            await asyncio.sleep(0.3)
            self.done.append(part.request)

    async def main():
        w = Worker()
        started = asyncio.get_running_loop().time()
        task_id = await asyncio.wait_for(SidebandWorker.answer(w, "t1", "Do it all"), 0.1)
        assert task_id and asyncio.get_running_loop().time() - started < 0.1 and w.done == []
        await asyncio.sleep(0.5)
        assert w.done == ["Do it all"]

    asyncio.run(main())
