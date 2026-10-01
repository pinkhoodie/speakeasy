"""Visual cards ("views"): catalog validation, quick-answer cards, task blocks. Made-up data."""
from __future__ import annotations

from speakeasy import instant, markets, quick, views
from speakeasy.text import split_result


def test_unknown_kind_and_bad_fields_are_dropped():
    assert views.clean({"kind": "hologram", "x": 1}) is None
    v = views.clean({"kind": "quote", "symbol": "ACME", "price": "12.5", "logo_url": "http://x.example/a.png",
                     "points": [1, "two", 3, float("nan")], "secret": "nope"})
    assert v == {"kind": "quote", "symbol": "ACME", "price": 12.5, "points": [1.0, 3.0]}


def test_lists_are_capped_and_rows_validated():
    v = views.clean({"kind": "list", "title": "Errands", "items": [{"text": f"item {i}", "done": "yes"} for i in range(40)]})
    assert len(v["items"]) == 20 and "done" not in v["items"][0]


def test_task_block_is_stripped_and_becomes_views():
    out = ("Here's the spot.\n```speakeasy-views\n[{\"kind\": \"place\", \"name\": \"Corner Cafe\", \"rating\": 4.6,"
           " \"lat\": 1.5, \"lon\": 2.5}]\n```\nSPOKEN: Corner Cafe is open.")
    result = split_result(out, ())
    assert result["views"] == [{"kind": "place", "name": "Corner Cafe", "rating": 4.6, "lat": 1.5, "lon": 2.5}]
    assert "speakeasy-views" not in result["full"]


def test_clock_and_math_views():
    clock = instant.view("what time is it in London")
    assert clock["kind"] == "clock" and clock["zones"][-1]["here"] is True and len(clock["zones"]) == 2
    assert instant.view("what's 15 percent of 80") == {"kind": "math", "expression": "15% of 80", "result": "12"}


def test_market_subjects():
    assert markets.subject("where is acme at") is None
    assert markets.subject("how is eth doing today") == ("coin", "ETH")
    assert markets.subject("what did nvidia close at") == ("stock", "NVDA")
    assert markets.subject("how tall is the tower") is None


def test_stock_card_from_feed():
    def fake(url):
        assert "assetclass=stocks" in url
        return {"data": {"symbol": "ACME", "company": "Acme Rockets, Inc. Common Stock", "lastSalePrice": "$10.00",
                         "netChange": "-0.50", "percentageChange": "-4.76%", "previousClose": "$10.50",
                         "chart": [{"y": 10.5}, {"y": 10.2}, {"y": 10.0}]}}
    c = markets.stock_card("ACME", fake)
    assert c["name"] == "Acme Rockets" and c["price"] == 10.0 and c["change_pct"] == -4.76 and c["points"] == [10.5, 10.2, 10.0]


def test_views_in_results():
    rs = [{"title": "a", "text": "b", "view": {"kind": "fact", "title": "Height", "value": "828 m"}}, {"title": "c", "text": "d"}]
    assert quick.views_in(rs) == [{"kind": "fact", "title": "Height", "value": "828 m"}]
