"""The shaper may add one picture of an answer's data when the agent drew none (any topic)."""
from speakeasy import router


def _shape(visual, **extra):
    import json
    return router.clean_shape(json.dumps({"type": "answer", "headline": "h", "checks": [], "shipped": [],
                                          "open": [], "sections": [], "undo": False, "visual": visual, **extra}))


def test_a_two_sided_projection_becomes_a_game_card():
    shape = _shape({"kind": "game", "league": "League", "status": "Projected", "detail": "64% to win",
                    "teams": [{"name": "Your team", "score": "118.4", "winner": True},
                              {"name": "Opponent", "score": "97.2", "winner": False}]})
    assert shape and shape["_visual"]["kind"] == "game" and shape["_visual"]["status"] == "projected"
    assert [t["score"] for t in shape["_visual"]["teams"]] == ["118.4", "97.2"]


def test_only_data_kinds_and_valid_cards_pass():
    assert _shape({"kind": "weather", "temp": 60}) is None             # not a data kind for the shaper
    assert _shape({"kind": "game", "teams": [{"name": "one"}]}) is None  # a matchup needs two sides
    assert _shape(None) is None
    assert _shape({"kind": "list", "title": "Picks", "items": [{"text": "A"}, {"text": "B"}]})["_visual"]["kind"] == "list"


def test_a_visual_never_replaces_report_parts():
    shape = _shape({"kind": "stats", "items": [{"label": "Runs", "value": "8"}]},
                   sections=[{"title": "S", "points": ["p"]}])
    assert shape["sections"] and shape["_visual"]["kind"] == "stats"


def test_the_prompt_asks_for_fit_not_topics():
    assert "never a format tied to a topic" in router._SHAPE_PROMPT
