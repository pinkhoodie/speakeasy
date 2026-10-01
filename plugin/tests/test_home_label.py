from speakeasy.delivery import home_label

CHATS = [{"id": "11", "name": "general", "guild": "Pond Club", "type": "channel"},
         {"id": "11", "name": "Pond Club / #general", "type": "group"},
         {"id": "12", "name": "tasks", "guild": "Pond Club", "type": "channel"}]


def test_home_names_the_real_channel():
    assert home_label({"chat_id": "11", "name": "Home"}, CHATS) == "Home · Pond Club / general"


def test_home_unknown_channel_keeps_its_name():
    assert home_label({"chat_id": "99", "name": "Home"}, CHATS) == "Home"


def test_no_repeat_when_home_is_named_after_the_channel():
    assert home_label({"chat_id": "12", "name": "Pond Club / tasks"}, CHATS) == "Pond Club / tasks"
