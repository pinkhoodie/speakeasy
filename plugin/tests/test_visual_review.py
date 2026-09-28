"""Visual review: finished images wait on a review card until dismissed (across calls), the task
prompt asks for rendered results, the live 'looking at' image, and spoken 'show me'."""
from __future__ import annotations

import json

from fakes import SDP, http, wait_for

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def _image(home, name: str = "design.png"):
    folder = home / "cache" / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(PNG)
    return path


def _finished_with_images(store, key: str, run_id: str, n: int = 2, settled_offset: float = 0.0) -> None:
    import time
    store.reserve_run(key, "vi_" + key, "item_" + key, 1)
    store.update_run(key, run_id, "completed")
    cards = [{"kind": "image", "name": f"design-{i}.png", "path": f"/nowhere/design-{i}.png"} for i in range(n)]
    store.set_result(key, {"spoken": "Here it is.", "full": "Done.", "cards": cards})
    if settled_offset:
        with store._lock, store._db:
            store._db.execute("UPDATE runs SET settled_at=? WHERE idem_key=?", (time.time() + settled_offset, key))


# -- task prompt ------------------------------------------------------------------------------------

def test_task_prompt_asks_for_rendered_visual_results():
    from speakeasy.prompt import builder as P
    prompt = P.build_task_prompt(P.Names("Hermes", "Sam"), 1, "User: design a landing page")
    assert "render or screenshot the finished result" in prompt
    assert "MEDIA:<absolute path>" in prompt and "what you are looking at" in prompt
    assert "{user_name}" not in prompt and "Sam can review it" in prompt


# -- store: review state survives calls, capped to the latest three ---------------------------------

def test_review_waits_until_dismissed_and_survives_new_calls(service):
    store = service.store
    _finished_with_images(store, "se_design", "run_design", n=2)
    work = store.work(idem_key="se_design")
    assert work["review"]["images"] == [1, 2]
    # a later call: the waiting review is carried onto its task list, like a waiting draft
    store.reserve_run("se_new", "vi_new", "item_new", 1)
    store.update_run("se_new", "run_new", "running")
    carried = [t for t in store.latest_tasks() if t["task_id"] == "item_se_design"]
    assert carried and carried[0]["review"]["images"] == [1, 2]
    assert all("path" not in c for c in carried[0]["result"]["cards"])  # never a raw path to the app
    assert store.dismiss_review("run_design", [1]) == [1]
    assert store.work(idem_key="se_design")["review"]["images"] == [2]
    store.dismiss_review("run_design")
    assert "review" not in store.work(idem_key="se_design")
    assert not [t for t in store.latest_tasks() if t["task_id"] == "item_se_design"]
    # the images stay in the task
    assert len(store.work(idem_key="se_design")["result"]["cards"]) == 2


def test_only_the_three_most_recent_reviews_are_carried(service):
    store = service.store
    for i in range(5):
        _finished_with_images(store, f"se_{i}", f"run_{i}", n=1, settled_offset=i)
    assert store.keys_with_pending_reviews() == ["se_2", "se_3", "se_4"]


def test_running_or_failed_tasks_have_no_review(service):
    store = service.store
    store.reserve_run("se_run", "vi_run", "item_run", 1)
    store.update_run("se_run", "run_run", "running")
    assert "review" not in store.work(idem_key="se_run")
    assert store.dismiss_review("run_run") == []


# -- HTTP: authenticated dismiss route -------------------------------------------------------------

def test_dismiss_route_is_authenticated_and_validated(server, service):
    _finished_with_images(service.store, "se_http", "run_http", n=2)
    url = "/voice/reviews/run_http/dismiss"
    assert http(server.base_url, "POST", url, {})[0] == 401
    assert http(server.base_url, "POST", url, {"cards": ["1"]}, server.token)[0] == 400
    assert http(server.base_url, "POST", url, {"cards": [9]}, server.token)[0] == 400
    assert http(server.base_url, "POST", url, {"extra": 1}, server.token)[0] == 400
    assert http(server.base_url, "POST", "/voice/reviews/run_nope/dismiss", {}, server.token)[0] == 404
    status, body = http(server.base_url, "POST", url, {"cards": [2]}, server.token)
    assert status == 200 and body["dismissed"] == [2]
    status, body = http(server.base_url, "POST", url, {}, server.token)
    assert status == 200 and body["dismissed"] == [1, 2]
    assert "review" not in service.store.work(run_id="run_http")


def test_finished_design_pops_a_review_card_end_to_end(server, service, hermes, home):
    design = _image(home)
    hermes.responder = lambda prompt, sid: f"Here's the landing page.\nMEDIA:{design}\nDONE: Design done\nSPOKEN: It's ready."
    status, session = http(server.base_url, "POST", "/voice/sessions", {"sdp": SDP}, server.token,
                           {"Idempotency-Key": "req_design_1"})
    assert status == 201, session
    worker = service.workers[-1]
    worker.delegate("call_design_1", "Design a landing page for the bakery")
    task = wait_for(lambda: next((t for t in service.interaction(session["interaction_id"]).feed.last["tasks"] or []
                                  if t.get("review")), None))
    assert task["review"]["images"] == [1]
    raw = json.dumps(task)
    assert str(design) not in raw and "MEDIA:" not in raw
    status, data = http(server.base_url, "GET", f"/voice/card-image/{task['run_id']}/1", token=server.token)
    assert status == 200 and data == PNG
    http(server.base_url, "POST", f"/voice/reviews/{task['run_id']}/dismiss", {}, server.token)
    wait_for(lambda: not any(t.get("review") for t in service.interaction(session["interaction_id"]).feed.last["tasks"]))
