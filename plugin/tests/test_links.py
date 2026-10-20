from pathlib import Path

from speakeasy import views
from speakeasy.text import links_in, split_result


def test_links_come_from_what_the_agent_wrote():
    text = ("The reveal starts at 8:55 PM ET on [the event page](https://www.example.com/roadster). "
            "Replay later at https://video.example.org/live/123. Also see [the event page](https://www.example.com/roadster).")
    assert links_in(text) == [
        {"title": "the event page", "url": "https://www.example.com/roadster", "host": "example.com"},
        {"title": "video.example.org", "url": "https://video.example.org/live/123", "host": "video.example.org"}]
    assert links_in("example.net/launch with no scheme, http://insecure.example.com") == []


def test_answer_carries_links_and_list_rows_keep_theirs():
    out = ("Starts at 8:55 PM ET. [Watch live](https://www.example.com/live)\n"
           "```speakeasy-views\n[{\"kind\": \"list\", \"title\": \"Schedule\", \"items\": [{\"text\": \"Reveal\", "
           "\"detail\": \"Thu 8:55 PM\", \"url\": \"https://www.example.com/live\"}]}]\n```\nSPOKEN: It starts at 8:55.")
    result = split_result(out, (Path("/nonexistent"),))
    assert result["links"][0]["url"] == "https://www.example.com/live"
    assert result["views"][0]["items"][0]["url"] == "https://www.example.com/live"
    cleaned = views.clean_all([{"kind": "calendar_day", "date": "Thu", "events": [
        {"start": "8:55 PM", "title": "Reveal", "url": "https://www.example.com/live"}]}])
    assert cleaned and cleaned[0]["events"][0]["url"] == "https://www.example.com/live"
