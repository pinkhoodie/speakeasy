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


def test_a_warning_is_never_cut_from_what_the_voice_says():
    from speakeasy.text import spoken_from
    full = ("The porch lights are on.\n- **Brightness:** 60%, same as last night.\n- **Scene:** Evening.\n"
            "The schedule that turns them off at midnight failed after the update. I set a one-off timer instead.")
    said = spoken_from(full)
    assert said.startswith("The porch lights are on.") and "failed after the update" in said
    assert "**" not in said and "- " not in said
    assert spoken_from("Done. Everything synced. Nothing else to report.") == "Done. Everything synced."


def _modes(mode):
    from speakeasy.settings import validate_delivery
    return {"delivery": validate_delivery({"target": "discord:100", "new_thread": True, "mode": mode, "channels": [
        {"target": "discord:1", "label": "home-lab", "topic": "house"},
        {"target": "discord:2", "label": "work", "topic": "jobs"}]})}


def test_where_modes():
    from speakeasy.settings import validate_delivery, SettingsError
    import pytest
    assert validate_delivery({"target": "none", "new_thread": False, "channels": []})["mode"] == "home"
    with pytest.raises(SettingsError):
        validate_delivery({"target": "none", "new_thread": False, "channels": [], "mode": "everywhere"})
    # new work: only "topic" follows the routing model's pick
    assert channels.resolve(_modes("topic"), "voice", "work").channel.label == "work"
    assert channels.resolve(_modes("home"), "voice", "work").channel.default
    assert channels.resolve(_modes("single"), "voice", "work").channel.default
    # naming a channel works except in "single"
    assert channels.explicit("put this in #work", _modes("home")).channel.label == "work"
    assert channels.explicit("put this in #work", _modes("single")) is None
    # continuing existing chats: home + approved (and threads in them); "single" = home only
    home, single = _modes("home"), _modes("single")
    assert channels.allows_conversation(home, "discord", "999", "2", "thread")       # thread in #work
    assert channels.allows_conversation(home, "discord", "100", "", "group")         # the home channel
    assert not channels.allows_conversation(home, "discord", "555", "", "group")     # not approved
    assert not channels.allows_conversation(home, "telegram", "7", "", "dm")
    assert not channels.allows_conversation(single, "discord", "999", "2", "thread")
    assert channels.allows_conversation(single, "discord", "998", "100", "thread")   # thread in home
