"""Stock and crypto prices for quick answers, with chart data for the app card.

Stocks come from Nasdaq public quote data (no key, about 1-2 s, includes the day chart).
Crypto comes from CoinGecko public prices (no key, about 0.1 s).
Anything else returns no facts, and the normal web search runs instead.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)

TIMEOUT_S = 3.5
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
NASDAQ = "https://api.nasdaq.com/api"
COINGECKO = "https://api.coingecko.com/api/v3"
Fetch = Callable[[str], Any]


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


# Common spoken names that the symbol search gets wrong or slowly.
TICKERS = {
    "apple": "AAPL", "microsoft": "MSFT", "google": "GOOGL", "alphabet": "GOOGL", "amazon": "AMZN",
    "meta": "META", "facebook": "META", "nvidia": "NVDA", "tesla": "TSLA", "netflix": "NFLX",
    "rivian": "RIVN", "amd": "AMD", "intel": "INTC", "coinbase": "COIN", "robinhood": "HOOD",
    "palantir": "PLTR", "uber": "UBER", "airbnb": "ABNB", "spotify": "SPOT", "disney": "DIS",
    "s&p": "SPY", "s and p": "SPY", "the s&p 500": "SPY", "nasdaq": "QQQ", "the nasdaq": "QQQ",
    "dow": "DIA", "the dow": "DIA", "berkshire": "BRK.B", "walmart": "WMT", "costco": "COST",
}
COINS = {
    "bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum", "ether": "ethereum",
    "solana": "solana", "sol": "solana", "dogecoin": "dogecoin", "doge": "dogecoin", "xrp": "ripple",
    "cardano": "cardano", "usdc": "usd-coin", "hype": "hyperliquid", "hyperliquid": "hyperliquid",
}
MARKET_WORDS = re.compile(r"(?i)\b(stock|stocks|share|shares|price|trading|trade|at|worth|market|ticker|"
                          r"closed?|open(?:ed)?|up|down|doing|today)\b")


def _num(text: Any) -> float | None:
    try:
        return float(str(text).replace("$", "").replace(",", "").replace("%", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def subject(question: str) -> tuple[str, str] | None:
    """("stock", "AAPL") or ("coin", "ethereum") for a market question, else None."""
    q = " " + (question or "").lower().replace("'s", "") + " "
    if not MARKET_WORDS.search(q):
        return None
    for name, coin in sorted(COINS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(name)}\b", q):
            return ("coin", coin)
    for name, ticker in sorted(TICKERS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![\w&]){re.escape(name)}(?![\w&])", q):
            return ("stock", ticker)
    m = re.search(r"\b(?:ticker|symbol)\s+([a-z]{1,5})\b|\$([a-z]{1,5})\b", q)
    if m:
        return ("stock", (m.group(1) or m.group(2)).upper())
    return None


def _thin(points: list[float], keep: int = 60) -> list[float]:
    if len(points) <= keep:
        return points
    step = len(points) / keep
    return [points[int(i * step)] for i in range(keep - 1)] + [points[-1]]


def clean_name(name: str) -> str:
    """'Rivian Automotive, Inc. Class A Common Stock' -> 'Rivian Automotive'."""
    name = re.sub(r"\b(Common Stock|Ordinary Shares|American Depositary Shares|Class [A-C]\b.*)", "", name)
    name = re.sub(r",?\s+(Inc\.?|Corporation|Corp\.?|Co\.?|Ltd\.?|plc|N\.V\.|S\.A\.)\s*$", "", name.strip())
    return name.strip(" ,") or name


def stock_card(ticker: str, fetch: Fetch = _get) -> dict[str, Any] | None:
    q = urllib.parse.quote(ticker)
    chart: dict[str, Any] = {}
    for asset_class in ("stocks", "etf"):
        try:
            chart = (fetch(f"{NASDAQ}/quote/{q}/chart?assetclass={asset_class}") or {}).get("data") or {}
        except Exception as exc:
            logger.info("speakeasy: stock lookup failed (%s)", type(exc).__name__)
            chart = {}
        if _num(chart.get("lastSalePrice")) is not None:
            break
    price = _num(chart.get("lastSalePrice"))
    if price is None:
        return None
    points = [float(p["y"]) for p in (chart.get("chart") or []) if isinstance(p, dict) and p.get("y") is not None]
    return {"kind": "quote", "asset": "stock", "symbol": chart.get("symbol") or ticker,
            "name": clean_name(str(chart.get("company") or ticker)),
            "price": price, "currency": "USD", "change": _num(chart.get("netChange")),
            "change_pct": _num(chart.get("percentageChange")), "previous_close": _num(chart.get("previousClose")),
            "as_of": str(chart.get("timeAsOf") or ""), "exchange": str(chart.get("exchange") or ""),
            "points": _thin(points)}


def coin_card(coin: str, fetch: Fetch = _get) -> dict[str, Any] | None:
    try:
        rows = fetch(f"{COINGECKO}/coins/markets?vs_currency=usd&ids={urllib.parse.quote(coin)}&sparkline=false")
        hist = fetch(f"{COINGECKO}/coins/{urllib.parse.quote(coin)}/market_chart?vs_currency=usd&days=1")
    except Exception as exc:
        logger.info("speakeasy: crypto lookup failed (%s)", type(exc).__name__)
        return None
    row = rows[0] if isinstance(rows, list) and rows else None
    if not row or row.get("current_price") is None:
        return None
    points = [float(p[1]) for p in (hist or {}).get("prices") or [] if isinstance(p, list) and len(p) == 2]
    return {"kind": "quote", "asset": "crypto", "symbol": str(row.get("symbol") or coin).upper(),
            "name": str(row.get("name") or coin), "price": float(row["current_price"]), "currency": "USD",
            "change": row.get("price_change_24h"), "change_pct": row.get("price_change_percentage_24h"),
            "as_of": "last 24 hours", "logo_url": row.get("image") if str(row.get("image", "")).startswith("https://") else None,
            "points": _thin(points)}


def card(question: str, fetch: Fetch = _get) -> dict[str, Any] | None:
    found = subject(question)
    if not found:
        return None
    kind, key = found
    return coin_card(key, fetch) if kind == "coin" else stock_card(key, fetch)


def facts_from(card_: dict[str, Any]) -> list[dict[str, str]]:
    sign = lambda v: "" if v is None else (f"+{v:,.2f}" if v >= 0 else f"{v:,.2f}")  # noqa: E731
    line = (f"{card_['name']} ({card_['symbol']}): ${card_['price']:,.2f}, change {sign(card_.get('change'))} "
            f"({sign(card_.get('change_pct'))}%) {card_.get('as_of') or ''}.")
    extra = f"Previous close ${card_['previous_close']:,.2f}." if card_.get("previous_close") else "Live price."
    return [{"title": f"{card_['name']} price", "text": line}, {"title": "Market data", "text": extra}]
