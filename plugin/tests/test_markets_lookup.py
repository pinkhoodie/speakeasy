"""Coin and stock names outside the short built-in lists are looked up live, never answered from search."""
from speakeasy import markets, quick

ASSETS = {"ZPH": "Zephyr Token", "ETH": "Ether", "QRK": "Quark Finance"}


def fake(url: str):
    if url.endswith("/products"):
        return [{"base_currency": s, "quote_currency": "USD", "status": "online"} for s in ASSETS]
    if url.endswith("/currencies"):
        return [{"id": s, "name": n} for s, n in ASSETS.items()]
    if "/search?query=" in url:
        return {"coins": [{"id": "zephyr-token", "name": "Zephyr Token", "symbol": "zph", "market_cap_rank": 90}]}
    if "/coins/markets" in url and "zephyr-token" in url:
        return [{"symbol": "zph", "name": "Zephyr Token", "current_price": 12.5, "price_change_24h": -0.5,
                 "price_change_percentage_24h": -3.8, "image": "https://img.example/zph.png"}]
    if "/market_chart" in url:
        return {"prices": [[i, 12 + i / 100] for i in range(50)]}
    if "/products/QRK-USD/stats" in url:
        return {"last": "0.00000437", "open": "0.00000430"}
    if "/products/QRK-USD/candles" in url:
        return [[i, 0, 0, 0, 0.0000043, 1] for i in range(30)]
    if "autocomplete" in url:
        return {"data": []}
    raise AssertionError(url)


def test_token_by_spoken_name_gets_a_live_card():
    card = markets.card("What's the price of Zephyr token", fake)
    assert card and card["symbol"] == "ZPH" and card["price"] == 12.5 and card["points"]


def test_coin_falls_back_to_coinbase_when_coingecko_lacks_it():
    card = markets.card("what's quark trading at", lambda u: {"coins": []} if "/search" in u else fake(u))
    assert card and card["symbol"] == "QRK" and abs(card["price"] - 0.00000437) < 1e-12
    assert "$0.00000437" in markets.facts_from(card)[0]["text"]


def test_ordinary_questions_are_not_prices():
    assert markets.subject("how is Sam doing today", fake) is None
    assert markets.subject("is the market up today", fake) is None


def test_failed_price_lookup_never_falls_back_to_web_search(monkeypatch):
    monkeypatch.setattr(markets, "card", lambda q, fetch=None: None)
    monkeypatch.setattr(markets, "is_price_question", lambda q: True)
    monkeypatch.setattr(quick, "search", lambda q: (_ for _ in ()).throw(AssertionError("searched")))
    assert quick.facts("price of the zephyr token") == []
