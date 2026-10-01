"""Stock and crypto prices for quick answers, with chart data for the app card.

Stocks come from Nasdaq public quote data (no key, about 1-2 s, includes the day chart).
Crypto comes from CoinGecko public prices (no key, about 0.1 s).
Names not in the short lists below are looked up by name in both. Anything else returns no facts,
and the normal web search runs instead.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)

TIMEOUT_S = 3.5
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
NASDAQ = "https://api.nasdaq.com/api"
COINGECKO = "https://api.coingecko.com/api/v3"
COINBASE = "https://api.exchange.coinbase.com"
Fetch = Callable[[str], Any]
ASSETS_TTL_S = 24 * 3600
_assets: dict[str, Any] = {"at": 0.0, "by_symbol": {}}


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
    "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH", "ether": "ETH",
    "solana": "SOL", "sol": "SOL", "dogecoin": "DOGE", "doge": "DOGE", "xrp": "XRP", "ripple": "XRP",
    "cardano": "ADA", "usdc": "USDC", "hype": "HYPE", "hyperliquid": "HYPE",
}
MARKET_WORDS = re.compile(r"(?i)\b(stock|stocks|share|shares|price|trading|trade|at|worth|market|ticker|"
                          r"closed?|open(?:ed)?|up|down|doing|today)\b")


def _num(text: Any) -> float | None:
    try:
        return float(str(text).replace("$", "").replace(",", "").replace("%", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


CRYPTO_WORDS = re.compile(r"(?i)\b(token|tokens|coin|coins|crypto|cryptocurrency)\b")
STOCK_WORDS = re.compile(r"(?i)\b(stock|stocks|share|shares|ticker)\b")
# Words around the asset's name in a price question; whatever is left is the name to look up.
FILLER = set("""what whats what's is are the a an of for price prices priced how hows how's where wheres where's
doing trading trade traded at worth today right now currently current market value up down tell me give check
on in its it's please quick quote token tokens coin coins crypto cryptocurrency stock stocks share shares ticker
closed close open opened this morning yesterday s and much does cost""".split())


def asset_name(question: str) -> str:
    """'What's the price of Venice token' -> 'venice'. Empty when nothing like a name is left."""
    words = re.findall(r"[a-z0-9&.\-]+", (question or "").lower().replace("'s", " "))
    left = [w for w in words if w not in FILLER]
    return " ".join(left[:3]) if 0 < len(left) <= 3 else ""


def coinbase_assets(fetch: "Fetch | None" = None) -> dict[str, str]:
    """Symbol -> name for every coin with a live US-dollar market on Coinbase. Fetched once a day."""
    if _assets["by_symbol"] and time.time() - _assets["at"] < ASSETS_TTL_S and fetch is None:
        return _assets["by_symbol"]
    get = fetch or _get
    try:
        products = get(f"{COINBASE}/products") or []
        currencies = get(f"{COINBASE}/currencies") or []
    except Exception as exc:
        logger.info("speakeasy: coin list unavailable (%s)", type(exc).__name__)
        return _assets["by_symbol"]
    live = {str(p.get("base_currency", "")).upper() for p in products
            if isinstance(p, dict) and p.get("quote_currency") == "USD" and p.get("status") == "online"
            and not p.get("trading_disabled")}
    names = {str(c.get("id", "")).upper(): str(c.get("name") or c.get("id")) for c in currencies if isinstance(c, dict)}
    by_symbol = {sym: names.get(sym, sym) for sym in live if sym}
    if fetch is None and by_symbol:
        _assets.update(at=time.time(), by_symbol=by_symbol)
    return by_symbol


def _plain(name: str) -> str:
    return re.sub(r"\s+(token|coin|finance|protocol|network)$", "", name.lower().strip())


def coin_symbol(name: str, assets: dict[str, str], bare_symbol_ok: bool = True) -> str | None:
    """'venice' / 'venice token' / 'aerodrome' / 'link' -> the Coinbase symbol, else None."""
    want = _plain(name)
    if not want:
        return None
    for sym, full in assets.items():
        if want in (_plain(full), full.lower()):
            return sym
    if bare_symbol_ok and want.upper() in assets and re.fullmatch(r"[a-z0-9]{2,6}", want):
        return want.upper()
    for sym, full in assets.items():
        if len(want) >= 4 and _plain(full).startswith(want + " "):
            return sym
    return None


def subject(question: str, fetch: "Fetch | None" = None) -> tuple[str, str] | None:
    """("stock", "AAPL"), ("coin", "ETH"), ("gecko", "some-id") or ("name", "...") for a market question,
    else None. "name": nothing matched offhand, so `card` searches the stock list for it."""
    q = " " + (question or "").lower().replace("'s", "") + " "
    if not (MARKET_WORDS.search(q) or CRYPTO_WORDS.search(q)):
        return None
    stock_said = bool(STOCK_WORDS.search(q))
    crypto_said = bool(CRYPTO_WORDS.search(q))
    for name, coin in sorted(COINS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(name)}\b", q) and not stock_said:
            return ("coin", coin)
    for name, ticker in sorted(TICKERS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![\w&]){re.escape(name)}(?![\w&])", q) and not crypto_said:
            return ("stock", ticker)
    m = re.search(r"\b(?:ticker|symbol)\s+([a-z]{1,5})\b|\$([a-z]{1,5})\b", q)
    if m:
        return ("stock", (m.group(1) or m.group(2)).upper())
    # Only a real price question goes on to a name search ("how's Sam doing" must not).
    if not re.search(r"(?i)\b(price|priced|trading|worth|quote|token|coin|crypto|stock|shares?|ticker|market cap)\b", q):
        return None
    name = asset_name(question)
    if not name:
        return None
    if not stock_said:
        sym = coin_symbol(name, coinbase_assets(fetch))
        if sym:
            return ("coin", sym)
    if crypto_said:
        return ("gecko", name)
    return ("name", name)


def find_coin(name: str, fetch: "Fetch", strict: bool) -> str | None:
    """CoinGecko id for a spoken coin name or symbol. Strict (no token/coin word said) needs an exact
    name/symbol match on a ranked coin, so ordinary words don't turn into obscure tokens."""
    try:
        coins = (fetch(f"{COINGECKO}/search?query={urllib.parse.quote(name)}") or {}).get("coins") or []
    except Exception as exc:
        logger.info("speakeasy: coin search failed (%s)", type(exc).__name__)
        return None
    want = name.lower().replace(" token", "").strip()
    best = None
    for c in coins[:10]:
        cname, sym, rank = str(c.get("name", "")).lower(), str(c.get("symbol", "")).lower(), c.get("market_cap_rank")
        exact = want in (cname, sym) or cname in (f"{want} token", f"{want} coin")
        loose = cname.startswith(want) or sym == want
        if not (exact or (loose and not strict)) or (strict and not rank):
            continue
        if strict and rank and rank > 500:
            continue
        key = (0 if exact else 1, rank or 10**6)
        if best is None or key < best[0]:
            best = (key, c.get("id"))
    return best[1] if best else None


def find_stock(name: str, fetch: "Fetch", strict_symbol: bool = False) -> str | None:
    """Ticker for a company name whose listed name starts with what was said ('snowflake' -> SNOW)."""
    try:
        rows = (fetch(f"{NASDAQ}/autocomplete/slookup/5?search={urllib.parse.quote(name)}") or {}).get("data") or []
    except Exception as exc:
        logger.info("speakeasy: stock search failed (%s)", type(exc).__name__)
        return None
    for r in rows:
        if str(r.get("asset", "")).upper() == "STOCKS" and clean_name(str(r.get("name", ""))).lower().startswith(name.lower()):
            return str(r.get("symbol") or "") or None
    if strict_symbol and re.fullmatch(r"[a-z]{1,5}", name):
        for r in rows:
            if str(r.get("symbol", "")).lower() == name:
                return str(r["symbol"])
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


def coin_card(symbol: str, fetch: Fetch = _get) -> dict[str, Any] | None:
    """Live price, 24-hour change and the day's chart from Coinbase's public market data (~0.1 s)."""
    pair = urllib.parse.quote(f"{symbol.upper()}-USD")
    try:
        stats = fetch(f"{COINBASE}/products/{pair}/stats") or {}
        candles = fetch(f"{COINBASE}/products/{pair}/candles?granularity=900") or []
    except Exception as exc:
        logger.info("speakeasy: coin price failed (%s)", type(exc).__name__)
        return None
    last, opened = _num(stats.get("last")), _num(stats.get("open"))
    if last is None:
        return None
    rows = sorted((c for c in candles if isinstance(c, list) and len(c) >= 5), key=lambda c: c[0])[-96:]
    points = [float(c[4]) for c in rows]
    change = (last - opened) if opened else None
    pct = (change / opened * 100) if opened else None
    name = coinbase_assets().get(symbol.upper()) or symbol.upper()
    return {"kind": "quote", "asset": "crypto", "symbol": symbol.upper(), "name": name, "price": last,
            "currency": "USD", "change": round(change, 6) if change is not None else None,
            "change_pct": round(pct, 2) if pct is not None else None, "as_of": "last 24 hours",
            "points": _thin(points)}


def gecko_card(coin: str, fetch: Fetch = _get) -> dict[str, Any] | None:
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


GECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "DOGE": "dogecoin", "XRP": "ripple",
             "ADA": "cardano", "USDC": "usd-coin", "HYPE": "hyperliquid", "VVV": "venice-token"}
_gecko_cache: dict[str, tuple[float, Any]] = {}
GECKO_CACHE_S = 60


def _gecko_fetch(fetch: Fetch) -> Fetch:
    """CoinGecko's free tier allows only a few requests a minute, so repeat asks reuse a 60-second answer."""
    def get(url: str) -> Any:
        hit = _gecko_cache.get(url)
        if hit and time.time() - hit[0] < GECKO_CACHE_S:
            return hit[1]
        value = fetch(url)
        _gecko_cache[url] = (time.time(), value)
        return value
    return get if fetch is _get else fetch


def crypto_card(name: str, symbol: str | None, fetch: Fetch = _get) -> dict[str, Any] | None:
    """CoinGecko first (it covers nearly every token); Coinbase when CoinGecko is busy or doesn't
    have it, so a token price is always live and never an old web snippet."""
    gget = _gecko_fetch(fetch)
    gid = GECKO_IDS.get((symbol or "").upper()) or find_coin(name, gget, strict=False)
    if gid:
        found = gecko_card(gid, gget)
        if found:
            return found
    if not symbol:
        symbol = coin_symbol(name, coinbase_assets(None if fetch is _get else fetch))
    return coin_card(symbol, fetch) if symbol else None


def card(question: str, fetch: Fetch = _get) -> dict[str, Any] | None:
    found = subject(question, None if fetch is _get else fetch)
    if not found:
        return None
    kind, key = found
    if kind == "stock":
        return stock_card(key, fetch)
    if kind == "coin":
        return crypto_card(asset_name(question) or key, key, fetch)
    if kind == "gecko":  # said "token"/"coin" and Coinbase doesn't list it
        return crypto_card(key, None, fetch)
    ticker = find_stock(key, fetch, strict_symbol=bool(STOCK_WORDS.search(question)))
    return stock_card(ticker, fetch) if ticker else None


def is_price_question(question: str) -> bool:
    """A market question we'd expect live data for. When the lookup fails anyway, the quick lane must
    not fall back to a web search: search snippets carry old prices said as if they were current."""
    found = subject(question)
    return bool(found) and found[0] != "name"  # "price of eggs" can still use the web


def money(value: float) -> str:
    """$84,159.09 / $0.7793 / $0.0000043700: enough digits to be meaningful at any size."""
    if value >= 1:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:.4f}"
    return f"${value:.10f}"


def facts_from(card_: dict[str, Any]) -> list[dict[str, str]]:
    sign = lambda v: "" if v is None else (f"+{v:,.2f}" if v >= 0 else f"{v:,.2f}")  # noqa: E731
    line = (f"{card_['name']} ({card_['symbol']}): {money(card_['price'])}, change {sign(card_.get('change_pct'))}% "
            + ("over the last 24 hours, live price." if card_.get("asset") == "crypto"
               else f"today, as of {card_.get('as_of') or 'now'}."))
    extra = f"Previous close {money(card_['previous_close'])}." if card_.get("previous_close") else "Live price."
    return [{"title": f"{card_['name']} price", "text": line}, {"title": "Market data", "text": extra}]
