"""Weather for quick answers: Open-Meteo forecast data (free, no key), about half a second.

"What's the weather tomorrow", "will it rain in Lisbon this weekend", "how cold is it in Chicago".
Returns facts in the same shape as search results; the quick-answer writer turns them into a sentence.
A question with no place uses the user's home place (settings ``fast_routing.home_place``, a city
name). Without a place or a home place, it returns [] and the normal web search runs.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Callable

logger = logging.getLogger(__name__)

FORECAST = "https://api.open-meteo.com/v1/forecast"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT_S = 3.0
WEATHER_WORDS = re.compile(r"(?i)\b(?:weather|forecast|rain|raining|rainy|snow|snowing|sunny|cloudy|umbrella|jacket|"
                           r"temperature|how (?:hot|cold|warm)|humid|humidity|windy|wind|storm|thunder|degrees)\b")
NOT_WEATHER = re.compile(r"(?i)\b(?:thermostat|ac|a/c|air conditioning|heater|heating|inside|indoors|in here|"
                         r"living room|bedroom|kitchen)\b")
PLACE = re.compile(r"(?i)\b(?:in|for|at|around|over in|out in)\s+(?P<place>(?!the morning|the evening|the afternoon|"
                   r"the weekend|this|next|tomorrow|today|tonight)[a-z][a-z .'\-]{1,40}?)(?=\s+(?:today|tonight|"
                   r"tomorrow|this|next|on|over|right now|now|later|during)\b|[?.!,]|$)")
CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
         61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
         71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
         82: "heavy rain showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
         96: "thunderstorms with hail", 99: "thunderstorms with hail"}
Fetch = Callable[[str], Any]
# WMO weather code -> a symbol name the apps map to their own icons (SF Symbols on Apple platforms).
ICONS = {0: "sun", 1: "sun", 2: "cloud_sun", 3: "cloud", 45: "fog", 48: "fog", 51: "drizzle", 53: "drizzle",
         55: "drizzle", 56: "sleet", 57: "sleet", 61: "rain", 63: "rain", 65: "heavy_rain", 66: "sleet", 67: "sleet",
         71: "snow", 73: "snow", 75: "snow", 77: "snow", 80: "rain", 81: "rain", 82: "heavy_rain", 85: "snow",
         86: "snow", 95: "storm", 96: "storm", 99: "storm"}


_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
# Places don't move; forecasts are refreshed by Open-Meteo about every 15 minutes.
GEOCODE_TTL_S = 7 * 24 * 3600
FORECAST_TTL_S = 10 * 60


def _fetch_once(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.7.1", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
        return json.loads(response.read(500_000) or b"{}")


def _get(url: str) -> Any:
    """One Open-Meteo request: from the cache when fresh, else fetched with one quick retry.
    A single slow reply used to drop the whole card (the call then fell back to a web search)."""
    ttl = GEOCODE_TTL_S if url.startswith(GEOCODE) else FORECAST_TTL_S
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        data = _fetch_once(url)
    except (TimeoutError, OSError):
        data = _fetch_once(url)
    with _cache_lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[url] = (time.monotonic(), data)
    return data


def warm(home_place: str) -> None:
    """Fetch the home place's forecast ahead of a call, so the first weather question is instant."""
    if home_place:
        try:
            facts("weather today", home_place)
        except Exception as exc:  # warming is best effort
            logger.info("speakeasy: weather warm-up failed (%s)", type(exc).__name__)


def is_weather(question: str) -> bool:
    return bool(WEATHER_WORDS.search(question)) and not NOT_WEATHER.search(question)


def place_in(question: str) -> str | None:
    m = PLACE.search(question)
    return m["place"].strip(" .") if m else None


def locate(place: str, fetch: Fetch = _get) -> dict[str, Any] | None:
    """A place's coordinates. "Neighborhood, City" ("Riverside, Springfield") picks the match whose
    region mentions the qualifier, or the one nearest the qualifier itself, not just the most populous
    namesake elsewhere."""
    coords = re.search(r"\((-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\)\s*$", place)
    if coords:  # "Riverside, Springfield (40.7, -73.9)": exact, no lookup
        name = place[:coords.start()].strip() or "home"
        return {"name": name, "lat": float(coords[1]), "lon": float(coords[2]),
                "country": "US" if -170 < float(coords[2]) < -50 and float(coords[1]) > 15 else ""}
    head, _, qualifier = (part.strip() for part in place.partition(","))
    data = fetch(f"{GEOCODE}?{urllib.parse.urlencode({'name': head or place, 'count': 10})}")
    results = [r for r in data.get("results") or [] if "latitude" in r]
    if not results:
        return None
    r = results[0]
    if qualifier:
        q = qualifier.lower()
        named = [x for x in results if any(q in str(x.get(k) or "").lower()
                                           for k in ("admin1", "admin2", "admin3", "admin4", "country", "country_code"))]
        if named:
            r = named[0]
        else:
            anchor = (fetch(f"{GEOCODE}?{urllib.parse.urlencode({'name': qualifier, 'count': 1})}").get("results") or [None])[0]
            if anchor:
                r = min(results, key=lambda x: (x["latitude"] - anchor["latitude"]) ** 2
                        + (x["longitude"] - anchor["longitude"]) ** 2)
    return {"name": (f"{r.get('name')}, {qualifier.title()}" if qualifier else r.get("name")) or place, "lat": r["latitude"], "lon": r["longitude"],
            "country": r.get("country_code") or ""}


def facts(question: str, home_place: str = "", fetch: Fetch = _get) -> list[dict[str, str]]:
    if not is_weather(question):
        return []
    place = place_in(question) or home_place
    if not place:
        return []
    try:
        where = locate(place, fetch)
        if where is None:
            return []
        fahrenheit = where["country"] in {"US", "LR", "MM", "BS", "BZ", "KY", "PW"}
        unit = "°F" if fahrenheit else "°C"
        params = {"latitude": where["lat"], "longitude": where["lon"], "timezone": "auto", "forecast_days": 7,
                  "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
                  "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                           "sunrise,sunset",
                  "hourly": "temperature_2m,precipitation_probability,weather_code",
                  "temperature_unit": "fahrenheit" if fahrenheit else "celsius",
                  "wind_speed_unit": "mph" if fahrenheit else "kmh"}
        data = fetch(f"{FORECAST}?{urllib.parse.urlencode(params)}")
    except Exception as exc:
        logger.info("speakeasy: weather lookup failed (%s)", type(exc).__name__)
        return []
    cur, daily, hourly = data.get("current") or {}, data.get("daily") or {}, data.get("hourly") or {}
    if not cur or not daily.get("time"):
        return []
    wind = "mph" if fahrenheit else "km/h"
    out = [{"title": f"Weather in {where['name']}",
            "text": (f"Now ({cur.get('time', '').replace('T', ' ')} local): {round(cur['temperature_2m'])}{unit}, "
                     f"feels like {round(cur.get('apparent_temperature', cur['temperature_2m']))}{unit}, "
                     f"{CODES.get(cur.get('weather_code'), 'mixed')}, wind {round(cur.get('wind_speed_10m', 0))} {wind}.")}]
    today = date.fromisoformat(daily["time"][0])
    for i, day in enumerate(daily["time"]):
        d = date.fromisoformat(day)
        label = "Today" if d == today else "Tomorrow" if d == today + timedelta(days=1) else f"{d:%A}"
        rise = (daily.get("sunrise") or [""] * 7)[i][11:16]
        sets = (daily.get("sunset") or [""] * 7)[i][11:16]
        out.append({"title": f"{label}, {d:%B} {d.day}",
                    "text": (f"{CODES.get(daily['weather_code'][i], 'mixed')}, high {round(daily['temperature_2m_max'][i])}"
                             f"{unit}, low {round(daily['temperature_2m_min'][i])}{unit}, chance of rain "
                             f"{daily['precipitation_probability_max'][i] or 0}%, sunrise {rise}, sunset {sets}.")})
    # The rest of today, every three hours (for "will it rain tonight", "later").
    now_key = cur.get("time", "")[:13]
    hours = [(t, hourly["temperature_2m"][i], hourly["precipitation_probability"][i], hourly["weather_code"][i])
             for i, t in enumerate(hourly.get("time") or []) if t[:13] >= now_key][:24:3]
    icon = lambda code: ICONS.get(code, "cloud")  # noqa: E731
    out[0]["view"] = {
        "kind": "weather", "place": where["name"].split(" (")[0], "temp": cur.get("temperature_2m"),
        "feels_like": cur.get("apparent_temperature"), "unit": unit, "condition": CODES.get(cur.get("weather_code"), "mixed"),
        "icon": icon(cur.get("weather_code")), "high": daily["temperature_2m_max"][0], "low": daily["temperature_2m_min"][0],
        "rain": daily["precipitation_probability_max"][0], "wind": f"{round(cur.get('wind_speed_10m', 0))} {wind}",
        "sunrise": (daily.get("sunrise") or [""])[0][11:16], "sunset": (daily.get("sunset") or [""])[0][11:16],
        "hours": [{"label": f"{datetime.fromisoformat(t):%-I %p}", "temp": temp, "rain": p or 0, "icon": icon(code)}
                  for t, temp, p, code in hours[:8]],
        "days": [{"label": "Today" if i == 0 else f"{date.fromisoformat(d):%a}", "high": daily["temperature_2m_max"][i],
                  "low": daily["temperature_2m_min"][i], "rain": daily["precipitation_probability_max"][i] or 0,
                  "condition": CODES.get(daily["weather_code"][i], "mixed"), "icon": icon(daily["weather_code"][i])}
                 for i, d in enumerate(daily["time"][:7])]}
    if hours:
        out.append({"title": "Next 24 hours", "text": "; ".join(
            f"{datetime.fromisoformat(t):%-I %p} {round(temp)}{unit} {CODES.get(code, 'mixed')} rain {p or 0}%"
            for t, temp, p, code in hours)})
    return out
