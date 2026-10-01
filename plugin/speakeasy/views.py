"""Visual cards ("views") the apps draw next to an answer.

A view is a small typed JSON object: {"kind": "quote", ...}. Quick answers build them from the live data
they already fetched (prices, weather, games, clocks); full Hermes tasks can add them in a fenced
`speakeasy-views` block, which is validated here and stripped from the text. Apps ignore kinds they
don't know, so new kinds never break an older app.

Every field is checked against the catalog below: strings are clipped, URLs must be https, lists are
capped, unknown fields are dropped. Nothing here fetches anything.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

MAX_VIEWS = 6
S, N, B, URL, ANY_LIST = "str", "num", "bool", "url", "list"

# Row shapes reused by several kinds.
EVENT = {"start": S, "end": S, "title": S, "place": S, "all_day": B, "color": S}
GAME_SIDE = {"name": S, "abbr": S, "score": S, "logo_url": URL, "winner": B, "home": B, "record": S}
DAY = {"label": S, "high": N, "low": N, "rain": N, "condition": S, "icon": S}
HOUR = {"label": S, "temp": N, "rain": N, "icon": S}
ROW_KV = {"label": S, "value": S}

# kind -> fields. A tuple value is (row shape, max rows) for a list of objects; ("str", n) a list of strings;
# ("num", n) a list of numbers.
CATALOG: dict[str, dict[str, Any]] = {
    # Markets
    "quote": {"asset": S, "symbol": S, "name": S, "price": N, "currency": S, "change": N, "change_pct": N,
              "previous_close": N, "as_of": S, "exchange": S, "logo_url": URL, "points": (N, 120),
              "range_label": S},
    # Weather
    "weather": {"place": S, "temp": N, "feels_like": N, "unit": S, "condition": S, "icon": S, "high": N, "low": N,
                "rain": N, "wind": S, "sunrise": S, "sunset": S, "hours": (HOUR, 12), "days": (DAY, 7)},
    # Sports
    "game": {"league": S, "status": S, "detail": S, "when": S, "venue": S, "broadcast": S,
             "teams": (GAME_SIDE, 2)},
    "games": {"title": S, "league": S, "games": ({"status": S, "when": S, "teams": (GAME_SIDE, 2)}, 16)},
    "standings": {"title": S, "rows": ({"rank": N, "name": S, "logo_url": URL, "record": S, "note": S}, 16)},
    # Time
    "clock": {"zones": ({"place": S, "time": S, "day": S, "offset": S, "zone": S, "here": B}, 6)},
    "timer": {"label": S, "ends_at": S, "seconds": N},
    "countdown": {"label": S, "date": S, "days": N, "detail": S},
    "calendar_day": {"date": S, "title": S, "events": (EVENT, 16), "free": (S, 8)},
    "agenda": {"title": S, "days": ({"date": S, "events": (EVENT, 10)}, 7)},
    "reminder": {"title": S, "due": S, "list": S, "done": B, "notes": S},
    # Places and getting around
    "place": {"name": S, "category": S, "address": S, "rating": N, "reviews": N, "price": S, "open_now": B,
              "hours_today": S, "phone": S, "url": URL, "photo_url": URL, "lat": N, "lon": N, "distance": S},
    "places": {"title": S, "items": ({"name": S, "category": S, "rating": N, "price": S, "open_now": B,
                                      "distance": S, "url": URL, "photo_url": URL, "lat": N, "lon": N}, 8)},
    "route": {"origin": S, "destination": S, "mode": S, "minutes": N, "distance": S, "leave_by": S, "arrive_by": S,
              "traffic": S, "steps": (S, 8), "lat": N, "lon": N, "dest_lat": N, "dest_lon": N},
    "transit": {"station": S, "status": S, "departures": ({"line": S, "color": S, "destination": S, "minutes": N,
                                                         "time": S, "note": S}, 10)},
    "flight": {"number": S, "airline": S, "origin": S, "destination": S, "departs": S, "arrives": S,
               "status": S, "gate": S, "terminal": S, "progress": N, "delay": S},
    "package": {"carrier": S, "item": S, "status": S, "eta": S, "url": URL,
                "steps": ({"when": S, "text": S, "done": B}, 8)},
    # People and messages
    "contact": {"name": S, "subtitle": S, "phone": S, "email": S, "photo_url": URL, "birthday": S, "note": S},
    "message_draft": {"channel": S, "to": S, "subject": S, "body": S, "status": S, "draft_id": S},
    "message": {"from": S, "channel": S, "when": S, "subject": S, "preview": S},
    "inbox": {"title": S, "items": ({"from": S, "subject": S, "preview": S, "when": S, "unread": B}, 8)},
    # Media
    "media": {"kind_label": S, "title": S, "subtitle": S, "year": S, "art_url": URL, "rating": S, "runtime": S,
              "where": (S, 6), "overview": S, "url": URL, "playing": B},
    "now_playing": {"title": S, "artist": S, "album": S, "art_url": URL, "playing": B, "position": N, "duration": N},
    # Knowledge
    "fact": {"title": S, "value": S, "unit": S, "subtitle": S, "image_url": URL, "source": S},
    "entity": {"title": S, "subtitle": S, "image_url": URL, "summary": S, "facts": (ROW_KV, 8), "url": URL},
    "definition": {"word": S, "phonetic": S, "part": S, "meanings": (S, 4), "example": S},
    "conversion": {"from_value": S, "from_unit": S, "to_value": S, "to_unit": S, "note": S},
    "math": {"expression": S, "result": S, "steps": (S, 6)},
    "translation": {"source_lang": S, "target_lang": S, "source": S, "text": S, "phonetic": S},
    "recipe": {"title": S, "image_url": URL, "time": S, "serves": S, "ingredients": (S, 20), "steps": (S, 12),
               "url": URL},
    "nutrition": {"title": S, "serving": S, "calories": N, "rows": (ROW_KV, 10)},
    # Comparing and lists
    "comparison": {"title": S, "columns": (S, 4), "rows": ({"label": S, "values": (S, 4)}, 10), "winner": S},
    "list": {"title": S, "items": ({"text": S, "done": B, "detail": S}, 20)},
    "steps": {"title": S, "items": (S, 12)},
    "stats": {"title": S, "items": ({"label": S, "value": S, "delta": S, "good": B}, 8)},
    "chart": {"title": S, "style": S, "unit": S, "labels": (S, 60), "series": ({"name": S, "values": (N, 60)}, 4)},
    "progress": {"label": S, "value": N, "total": N, "unit": S, "detail": S},
    "news": {"title": S, "items": ({"headline": S, "source": S, "when": S, "url": URL, "image_url": URL}, 6)},
    # Home
    "home": {"title": S, "devices": ({"name": S, "kind": S, "on": B, "level": N, "color": S, "state": S,
                                       "entity_id": S}, 16)},
    "thermostat": {"name": S, "current": N, "target": N, "mode": S, "unit": S, "humidity": N},
    "camera": {"name": S, "image_url": URL, "when": S},
}


def _str(value: Any, limit: int = 240) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit] if text else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _url(value: Any) -> str | None:
    text = _str(value, 600)
    if not text or not text.lower().startswith("https://") or " " in text:
        return None
    return text


def _field(shape: Any, value: Any) -> Any:
    if shape == S:
        return _str(value, 1200)
    if shape == N:
        return _num(value)
    if shape == B:
        return value if isinstance(value, bool) else None
    if shape == URL:
        return _url(value)
    if isinstance(shape, tuple) and isinstance(value, list):
        row, cap = shape
        out = []
        for item in value[:cap]:
            clean = _object(row, item) if isinstance(row, dict) else _field(row, item)
            if clean not in (None, {}):
                out.append(clean)
        return out or None
    return None


def _object(shape: dict[str, Any], raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, kind in shape.items():
        if key in raw:
            value = _field(kind, raw[key])
            if value is not None:
                out[key] = value
    return out


def clean(raw: Any) -> dict[str, Any] | None:
    """One validated view, or None for an unknown kind or an empty view."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    shape = CATALOG.get(kind) if isinstance(kind, str) else None
    if shape is None:
        return None
    body = _object(shape, raw)
    return {"kind": kind, **body} if body else None


def clean_all(raw: Any) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else [raw]
    return [v for v in (clean(x) for x in items[:MAX_VIEWS * 2]) if v][:MAX_VIEWS]


def extract(output: str) -> tuple[str, list[dict[str, Any]]]:
    """Strip an optional ```speakeasy-views block from a task's final text; return (text, views)."""
    match = re.search(r"(?s)\n?```speakeasy-views\s*\n(.*?)\n```", output or "")
    if not match:
        return output, []
    try:
        views = clean_all(json.loads(match.group(1)))
    except ValueError:
        views = []
    return (output[:match.start()] + output[match.end():]).strip(), views
