"""Placement stays invisible, and pointing back at what a task said continues that task."""
from speakeasy import channels, router
from speakeasy.calls import _same_words
from speakeasy.router import OpenTask


def settings():
    return {"delivery": {"target": "discord", "channels": [
        {"target": "discord:1", "label": "home", "topic": "house"},
        {"target": "discord:2", "label": "work", "topic": "jobs"}]}}


def test_a_room_is_not_a_channel_and_nothing_asks():
    assert channels.explicit("Put on some jazz in the den room", settings()) is None
    assert channels.explicit("post it in the zebra channel", settings()) is None   # unknown: no question
    both = channels.explicit("put this in #work and #home", settings())
    assert both is not None and not both.clarify and both.channel.label == "work"
    assert channels.explicit("put this in #home", settings()).channel.label == "home"


def test_pointing_back_continues_the_task_that_said_it():
    sprinkler = OpenTask("t1", "Water the garden", "completed",
                         "Done. Heads up: the timer script for the back sprinkler is failing since the update.", 90)
    other = OpenTask("t2", "Summarize the quarterly notes", "running", "Reading the notes", 30)
    hit = router.refers_back("ok can you repair that timer script it mentioned", [sprinkler, other])
    assert hit is not None and hit.task_id == "t1"
    assert router.refers_back("what's the forecast tomorrow", [sprinkler, other]) is None
    assert router.refers_back("repair the timer script", [sprinkler, other]) is None   # no pointing back: model decides
    old = OpenTask("t3", "Water the garden", "completed", "the timer script is failing", 3 * 3600)
    assert router.refers_back("fix that timer script it mentioned", [old]) is None      # too long ago


def test_decide_uses_it_without_the_model():
    t = OpenTask("t1", "Order coffee filters", "completed", "Ordered. The pantry tracker sheet is out of date.", 60)
    d = router.decide("update that pantry tracker you mentioned", [t], call=lambda m: None)
    assert d.parts[0].kind == "follow_up" and d.parts[0].task_id == "t1" and d.source == "refers_back"


def test_the_request_is_not_added_to_itself():
    assert _same_words("Dim the den lamps", "Dim the den lamps")
    assert not _same_words("and the hallway too", "Dim the den lamps")
