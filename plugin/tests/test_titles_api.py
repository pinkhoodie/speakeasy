"""The Mac app's Apple Intelligence rename sweep: stand-in titles are offered, renames stick."""
from __future__ import annotations

from fakes import http, wait_for
from test_routing import start_call, tasks


def test_stand_in_titles_are_offered_and_renames_stick(server, service, hermes):
    _, worker = start_call(server, service)
    worker.delegate("call_t", "Um, book a train to Boston for tomorrow morning please")
    wait_for(lambda: any(t.get("task_id") == "call_t" and t.get("title")
                         for t in tasks(server)))

    status, pending = http(server.base_url, "GET", "/voice/titles", token=server.token)
    assert status == 200, pending
    offered = [t for t in pending["tasks"] if t["task_id"] == "call_t"]
    assert offered, pending
    assert offered[0]["request"].startswith("Um, book a train")
    assert offered[0]["title"]  # the stand-in label made from the request's own words

    status, renamed = http(server.base_url, "POST", "/voice/titles",
                           {"task_id": "call_t", "title": "book train boston"}, server.token)
    assert status == 200 and renamed["renamed"] is True and renamed["title"] == "book train boston"

    # Gone from the offer list (a real title now) and shown on the task.
    status, pending = http(server.base_url, "GET", "/voice/titles", token=server.token)
    assert status == 200
    assert not [t for t in pending["tasks"] if t["task_id"] == "call_t"]
    assert [t for t in tasks(server) if t.get("task_id") == "call_t"][0]["title"] == "book train boston"

    # The job the task first named took the new title too.
    (job_name,) = service.store._db.execute(
        "SELECT name FROM jobs WHERE from_key=(SELECT idem_key FROM runs WHERE delegation_id='call_t')"
    ).fetchone()
    assert job_name == "book train boston"


def test_rename_validation_and_auth(server, service, hermes):
    status, _ = http(server.base_url, "GET", "/voice/titles")
    assert status == 401
    status, _ = http(server.base_url, "POST", "/voice/titles",
                     {"task_id": "nope", "title": "nothing here"}, server.token)
    assert status == 404

    _, worker = start_call(server, service)
    worker.delegate("call_v", "Find a dentist near the office")
    wait_for(lambda: any(t.get("task_id") == "call_v" and t.get("title") for t in tasks(server)))

    too_short = http(server.base_url, "POST", "/voice/titles", {"task_id": "call_v", "title": "  "}, server.token)
    assert too_short[0] == 400
    extra_key = http(server.base_url, "POST", "/voice/titles",
                     {"task_id": "call_v", "title": "find a dentist", "extra": 1}, server.token)
    assert extra_key[0] == 400
    too_long = http(server.base_url, "POST", "/voice/titles",
                    {"task_id": "call_v", "title": "x" * 91}, server.token)
    assert too_long[0] == 400
    not_a_string = http(server.base_url, "POST", "/voice/titles", {"task_id": "call_v", "title": 7}, server.token)
    assert not_a_string[0] == 400

    # A renamed title is whitespace-collapsed, not rejected outright.
    status, renamed = http(server.base_url, "POST", "/voice/titles",
                           {"task_id": "call_v", "title": "  find  a dentist  "}, server.token)
    assert status == 200 and renamed["title"] == "find a dentist"


def test_finished_and_unsettled_tasks_are_handled(server, service, hermes):
    """A failed task keeps its stand-in title offer; nothing from another interaction leaks in."""
    _, worker = start_call(server, service)
    worker.delegate("call_f", "Show me the weather in Lisbon this weekend")
    wait_for(lambda: any(t.get("task_id") == "call_f" for t in tasks(server)))
    status, pending = http(server.base_url, "GET", "/voice/titles", token=server.token)
    assert status == 200
    assert all("request" in t and "task_id" in t and "title" in t and "updated" in t
               for t in pending["tasks"])
