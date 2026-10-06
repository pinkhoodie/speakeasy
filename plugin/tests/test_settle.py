"""A greeting never starts work, and a breath mid-thought doesn't send half a request."""
import asyncio
import time
from collections import deque
from types import SimpleNamespace

from speakeasy import calls
from speakeasy.calls import is_greeting


def test_greetings_are_not_work():
    for text in ["Hey", "hi there", "Hello!", "yo", "what's up", "um", "Are you there?"]:
        assert is_greeting(text), text
    for text in ["Hey, dim the porch light", "hi can you check the parcel", "yo what's the forecast",
                 "No", "yes do it", "Okay so find me a quiet cafe"]:
        assert not is_greeting(text), text


def stub():
    return SimpleNamespace(fragments=deque(maxlen=512), absorbed=[])


def test_settle_adds_what_the_user_says_next(monkeypatch):
    monkeypatch.setattr(calls, "SETTLE_S", 0.4)
    s = stub()

    async def run():
        task = asyncio.ensure_future(calls.SidebandWorker.settle(s, "Is the documentary on the"))
        await asyncio.sleep(0.2)
        s.fragments.append({"speaker": "user", "text": "media server copying over to the tablet yet",
                            "at": time.monotonic()})
        return await task
    whole = asyncio.run(run())
    assert whole == "Is the documentary on the media server copying over to the tablet yet"
    # the voice's second handoff of those same words is recognised and not started twice
    assert calls.SidebandWorker._already_absorbed(s, "media server copying over to the tablet yet")
    assert not calls.SidebandWorker._already_absorbed(s, "order more coffee filters")


def test_settle_returns_quickly_when_nothing_follows(monkeypatch):
    monkeypatch.setattr(calls, "SETTLE_S", 0.3)
    s = stub()
    s.fragments.append({"speaker": "user", "text": "earlier words", "at": time.monotonic() - 5})
    started = time.monotonic()
    assert asyncio.run(calls.SidebandWorker.settle(s, "Book a table for two")) == "Book a table for two"
    assert time.monotonic() - started < 1.0
