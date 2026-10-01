"""Quick answers and optional Jev routing (made-up wording throughout)."""
from __future__ import annotations

import io
import json
from pathlib import Path

from speakeasy import jev, quick


def test_quick_eligible_only_for_public_questions():
    assert quick.eligible("How tall is the Space Needle")
    assert quick.eligible("Who won the hockey game last night")
    assert not quick.eligible("What's on my calendar Friday")
    assert not quick.eligible("Book a table for two at seven")
    assert not quick.eligible("Turn the porch light off")
    assert not quick.eligible("ok")


def test_quick_answer_or_none():
    results = [{"title": "Space Needle", "text": "605 feet tall"}, {"title": "Seattle", "text": "built 1962"}]
    assert quick.answer("How tall is the Space Needle", "today", lambda q: results,
                        lambda m: "The Space Needle is 605 feet tall.") == "The Space Needle is 605 feet tall."
    assert quick.answer("How tall is the Space Needle", "today", lambda q: results, lambda m: "UNSURE") is None
    assert quick.answer("How tall is the Space Needle", "today", lambda q: results[:1], lambda m: "x") is None
    assert quick.answer("How tall is the Space Needle", "today", lambda q: results,
                        lambda m: "**605 ft** see https://example.com") is None


def test_quick_question_regex():
    assert quick.QUESTION.search("so who owns the yankees")
    assert not quick.QUESTION.search("tell me a joke about otters")


def _fake(answers):
    return lambda *a, **k: answers


def test_jev_route_confident_and_unsure(tmp_path: Path):
    ok = {"route": {"choice": "quick", "confidence": 0.97}}
    assert jev.route(tmp_path, "venice", "How far is the moon", evaluator=_fake(ok))[0] == "quick"
    low = {"route": {"choice": "quick", "confidence": 0.5}}
    assert jev.route(tmp_path, "venice", "How far is the moon", evaluator=_fake(low))[0] is None
    assert jev.route(tmp_path, "venice", "x", evaluator=_fake(None))[0] is None


def test_jev_never_quick_when_about_a_running_task(tmp_path: Path):
    answers = {"route": {"choice": "quick", "confidence": 0.95}, "about_task": {"noul": 0.8}}
    lane, _, _ = jev.route(tmp_path, "venice", "is it open on sunday", ["find a brunch spot"], evaluator=_fake(answers))
    assert lane == "agent"


def test_jev_placement(tmp_path: Path):
    answers = {"several": {"noul": 0.02}, "show": {"noul": 0.1},
               "channel": {"choice": "Research", "confidence": 0.95},
               "conversation": {"choice": "none", "confidence": 0.99}}
    out = jev.placement(tmp_path, "venice", "look into heat pumps", [("Research", "deep dives")],
                        [("c1", "a chat about a bike")], evaluator=_fake(answers))
    assert out == {"show": False, "channel": "Research", "conversation": None}
    several = dict(answers, several={"noul": 0.9})
    assert jev.placement(tmp_path, "venice", "x", [], [], evaluator=_fake(several)) is None
    unsure = dict(answers, channel={"choice": "Research", "confidence": 0.4})
    assert jev.placement(tmp_path, "venice", "x", [("Research", "")], [], evaluator=_fake(unsure)) is None


def test_jev_evaluate_reads_key_and_parses(tmp_path: Path):
    (tmp_path / ".env").write_text("VENICE_API_KEY=test-not-real\n")
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(req, timeout):
        seen["url"], seen["auth"] = req.full_url, req.headers.get("Authorization")
        return Resp(json.dumps({"answers": {"route": {"choice": "home", "confidence": 1}}}).encode())

    out = jev.evaluate(tmp_path, "venice", "s", jev.ROUTE_QUESTION, opener=opener)
    assert out["route"]["choice"] == "home"
    assert seen["url"].endswith("/decisions") and seen["auth"] == "Bearer test-not-real"
    assert jev.evaluate(tmp_path, "openrouter", "s", {}, opener=opener) is None  # no key: off
    assert [p["id"] for p in jev.providers_status(tmp_path)] == ["venice", "openrouter", "typesafe"]


def _espn(url):
    if url.endswith("/teams?limit=500"):
        return {"sports": [{"leagues": [{"teams": [
            {"team": {"id": "7", "displayName": "Rivertown Otters", "shortDisplayName": "Otters", "name": "Otters",
                      "location": "Rivertown", "abbreviation": "RIV"}},
            {"team": {"id": "8", "displayName": "Hill City Owls", "shortDisplayName": "Owls", "name": "Owls",
                      "location": "Hill City", "abbreviation": "HCO"}}]}]}]}
    if "/schedule" in url:
        if "seasontype=3" in url:
            return {"events": []}
        return {"events": [{"id": "1", "date": "2020-05-01T23:00Z", "competitions": [{
            "status": {"type": {"completed": True, "state": "post"}},
            "competitors": [
                {"homeAway": "home", "winner": False, "score": {"displayValue": "2"}, "team": {"id": "8", "displayName": "Hill City Owls"}},
                {"homeAway": "away", "winner": True, "score": {"displayValue": "5"}, "team": {"id": "7", "displayName": "Rivertown Otters"}}]}]}]}
    if url.endswith("/teams/7"):
        return {"team": {"record": {"items": [{"summary": "10-4"}]}}}
    return {}


def test_scores_facts_from_feed():
    from speakeasy import scores
    scores._cache.clear()
    facts = scores.facts("did the otters win last night", fetch=_espn)
    text = " ".join(f["text"] for f in facts)
    assert "Rivertown Otters (away) 5" in text and "Hill City Owls (home) 2" in text
    assert "Rivertown Otters won" in text and "10-4" in text
    assert scores.facts("how tall is the tallest tower", fetch=_espn) == []
    assert scores.facts("tell me about otters", fetch=_espn) == []   # no game words: not a sports question


def test_scores_feed_down_is_no_facts():
    from speakeasy import scores
    scores._cache.clear()
    def broken(url):
        raise OSError("down")
    assert scores.facts("did the otters win", fetch=broken) == []
