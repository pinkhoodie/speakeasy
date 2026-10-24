"""Same-project fence: two voice tasks never edit one repo at once; questions are never fenced."""
from __future__ import annotations

import pytest

from fakes import wait_for
from speakeasy import calls, fence, router
from test_routing import start_call, tasks


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(calls, "SETTLE_S", 0.2)
    monkeypatch.setattr(calls, "FENCE_POLL_S", 0.05)
    # Routing without a model folds a request naming the same repo into the open task as a follow-up;
    # these tests are about a new task, so every request here is new work.
    monkeypatch.setattr(router, "decide", lambda request, *a, **k: router.Decision([router.Part(router.NEW, request)],
                                                                                 None, "test"))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "shopfront"
    (root / ".git").mkdir(parents=True)
    (root / "src").mkdir()
    return root


_MS = [0]


def say(worker, delegation_id, words, **extra):
    """One request after the voice's last reply (later in the call than the one before)."""
    _MS[0] += 10
    worker.feed({"type": "session.output_transcript.delta", "delta": "Okay.", "start_ms": _MS[0], "end_ms": _MS[0] + 1})
    worker.feed({"type": "session.input_transcript.delta", "delta": words, "start_ms": _MS[0] + 2, "end_ms": _MS[0] + 3})
    worker.feed({"type": "session.delegation.created", "offset_ms": _MS[0] + 4,
                 "delegation": {"id": delegation_id, "target": "client", **extra}})


def spoken(worker):
    return [c for k, _, c in worker.sent if k == "session.commentary.append"]


def run_of(worker, delegation_id):
    return wait_for(lambda: next((r for r in worker.interaction.runs.values()
                                  if r.delegation_id == delegation_id and (r.run_id or r.status == "waiting_on_task")), None))


def _two(server, service, hermes, repo, second=None):
    hermes.hold = True
    _, worker = start_call(server, service)
    say(worker, "call_a", f"Fix the checkout bug in {repo}")
    first = run_of(worker, "call_a")
    wait_for(lambda: len(hermes.calls) == 1)
    say(worker, "call_b", second or f"refactor the settings page in {repo}/src")
    held = run_of(worker, "call_b")
    return worker, first, held


# -- fence.py ------------------------------------------------------------------------------

def test_edit_and_read_only_requests():
    assert fence.is_edit_request("Fix the checkout bug in the shopfront repo")
    assert fence.is_edit_request("bump the version and commit it")
    for said in ["What changed in the shopfront repo?", "how's the shopfront repo doing", "review the shopfront repo",
                 "check the status of the build", "look at the shopfront project", "Find where checkout is handled"]:
        assert not fence.is_edit_request(said), said


def test_project_keys(repo):
    assert f"path:{repo.resolve()}" in fence.keys_in_text(f"fix it in {repo}/src/app.py")
    assert "name:shopfront" in fence.keys_in_text("update the shopfront repo")
    assert fence.keys_in_text("update the repo") == set()                  # no name, no fence
    assert fence.keys_in_text("fix it in /tmp/not-a-checkout/x") == set()  # not inside a git checkout
    keys, edits = fence.keys_from_tool("write_file", f"{repo}/src/a.py")
    assert edits and f"path:{repo.resolve()}" in keys
    keys, edits = fence.keys_from_tool("terminal", f"cd {repo} && npm test")
    assert not edits and f"name:{repo.name}" in keys
    assert fence.keys_from_tool("web_search", "shopfront") == (set(), False)


# -- the call ------------------------------------------------------------------------------

def test_a_second_edit_to_the_same_repo_waits_and_starts_when_the_first_finishes(server, service, hermes, repo):
    worker, first, held = _two(server, service, hermes, repo)
    wait_for(lambda: held.status == "waiting_on_task")
    line = wait_for(lambda: [c for c in spoken(worker) if "already working on" in c])[0]
    assert "shopfront" in line and "stop that one" in line and "anyway" in line
    card = wait_for(lambda: next((t for t in tasks(server) if t.get("waiting")), None))
    assert card["status"] == "waiting_on_task" and card["short_status"] == "Waiting for another task"
    assert card["waiting"]["project"] == "shopfront"
    time_calls = len(hermes.calls)
    assert time_calls == 1                                                   # not started on top of it
    hermes.runs[first.run_id]["done"].set()
    wait_for(lambda: len(hermes.calls) == 2)
    assert "settings page" in str(hermes.calls[1]["input"])
    assert not any("was stopped" in c or "failed" in c for c in spoken(worker))


def test_stop_the_other_one(server, service, hermes, repo):
    worker, first, held = _two(server, service, hermes, repo)
    wait_for(lambda: held.status == "waiting_on_task")
    say(worker, "call_stop", "stop the other one")
    wait_for(lambda: len(hermes.calls) == 2)
    assert hermes.runs[first.run_id]["status"] == "cancelled"
    wait_for(lambda: any("was stopped, so I've started" in c for c in spoken(worker)))


def test_start_anyway(server, service, hermes, repo):
    worker, first, held = _two(server, service, hermes, repo)
    wait_for(lambda: held.status == "waiting_on_task")
    say(worker, "call_go", "start it anyway")
    wait_for(lambda: len(hermes.calls) == 2)
    assert hermes.runs[first.run_id]["status"] == "running"                 # the first one keeps going


def test_a_failed_blocker_starts_it_and_says_so(server, service, hermes, repo):
    worker, first, held = _two(server, service, hermes, repo)
    wait_for(lambda: held.status == "waiting_on_task")
    hermes.runs[first.run_id]["events"] = [{"event": "run.failed", "error": "boom", "completed": False,
                                            "partial": False, "interrupted": False}]
    hermes.runs[first.run_id]["done"].set()
    wait_for(lambda: len(hermes.calls) == 2)
    wait_for(lambda: any("failed, so I've started" in c for c in spoken(worker)))


@pytest.mark.parametrize("second", ["explain what the code in {repo} does", "fix the flaky test in the payments repo",
                                    "Book a table for two at eight"])
def test_questions_and_other_work_are_never_fenced(server, service, hermes, repo, second):
    worker, first, held = _two(server, service, hermes, repo, second=second.format(repo=repo))
    wait_for(lambda: len(hermes.calls) == 2)
    assert held.status != "waiting_on_task"


def test_files_the_running_task_wrote_count(server, service, hermes, repo):
    hermes.hold = True
    hermes.live_events = [{"event": "tool.started", "tool": "write_file", "preview": f"{repo}/src/cart.py"}]
    _, worker = start_call(server, service)
    say(worker, "call_a", "Fix the flaky cart test")                        # names no project itself
    first = run_of(worker, "call_a")
    wait_for(lambda: first.edits)
    say(worker, "call_b", f"update the readme in {repo}")
    held = run_of(worker, "call_b")
    wait_for(lambda: held.status == "waiting_on_task")


@pytest.mark.parametrize("said,choice", [("start it anyway", "anyway"), ("run both at once", "anyway"),
                                         ("stop the other one", "stop"), ("cancel the first one", "stop"),
                                         ("just wait", "wait"), ("ok, wait for it", "wait"),
                                         ("what's the weather", None)])
def test_fence_choices(said, choice):
    assert router.fence_choice(said) == choice
