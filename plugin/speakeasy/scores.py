"""Sports scores for quick answers: ESPN's public scoreboard data (no key), ~0.1-0.3 s.

When a question names a team and sounds like it's about a game ("did the Mets win", "Knicks score",
"when do the Jets play"), this returns plain facts (last finished game, next game, record) that the
quick-answer writer turns into a sentence. Anything it can't match returns [] and the normal web
search runs instead. The feed is unofficial: any failure is just "no facts", never an error.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

BASE = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUES = {"baseball/mlb": "MLB", "basketball/nba": "NBA", "football/nfl": "NFL", "hockey/nhl": "NHL",
           "basketball/wnba": "WNBA", "football/college-football": "college football",
           "basketball/mens-college-basketball": "college basketball", "soccer/usa.1": "MLS",
           "soccer/eng.1": "Premier League"}
TIMEOUT_S = 2.0
TEAMS_TTL_S = 24 * 3600
GAME_WORDS = re.compile(r"(?i)\b(?:game|games|score|scores|won|win|wins|winning|lose|lost|losing|beat|beats|play|"
                        r"plays|playing|played|match|playoffs?|series|record|standings|next|tonight|last night|"
                        r"yesterday|final)\b")
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_lock = threading.Lock()

Fetch = Callable[[str], Any]


def _get(url: str) -> Any:
    # The feed turns away browser-like agents coming from scripts; a plain client agent is accepted.
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.7.1", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
        return json.loads(response.read(3_000_000) or b"{}")


def teams(league: str, fetch: Fetch = _get) -> list[dict[str, str]]:
    with _lock:
        hit = _cache.get(league)
        if hit and time.time() - hit[0] < TEAMS_TTL_S:
            return hit[1]
    data = fetch(f"{BASE}/{league}/teams?limit=500")
    out = []
    for item in (((data.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []):
        t = item.get("team") or {}
        if t.get("id"):
            out.append({k: str(t.get(k) or "") for k in ("id", "displayName", "shortDisplayName", "name", "location",
                                                       "abbreviation")})
    with _lock:
        _cache[league] = (time.time(), out)
    return out


def _names(team: dict[str, str]) -> list[str]:
    """Ways people say a team: full name, nickname ("Mets", "Red Sox"); city alone only when distinctive."""
    names = {team["displayName"], team["shortDisplayName"], team["name"]}
    return sorted({n.lower() for n in names if len(n) >= 3}, key=len, reverse=True)


def match(question: str, fetch: Fetch = _get) -> tuple[str, dict[str, str]] | None:
    """(league, team) named in a game question, or None. Pro leagues first; one clear match only."""
    if not GAME_WORDS.search(question):
        return None
    text = " " + re.sub(r"[^a-z0-9 ]", " ", question.lower()) + " "
    found: list[tuple[int, str, dict[str, str]]] = []
    for league in LEAGUES:
        try:
            for team in teams(league, fetch):
                for name in _names(team):
                    if f" {name} " in text:
                        found.append((len(name), league, team))
                        break
        except Exception as exc:
            logger.info("speakeasy: scores unavailable for %s (%s)", league, type(exc).__name__)
        if found and "college" not in league and "soccer" not in league:
            break  # a pro-league match wins; don't let a college namesake confuse it
    if not found:
        return None
    found.sort(key=lambda f: -f[0])
    best = found[0]
    if len(found) > 1 and found[1][0] == best[0] and found[1][2]["id"] != best[2]["id"]:
        return None  # two teams called the same thing: let the web search sort it out
    return best[1], best[2]


def _local(iso: str) -> str:
    """'2026-10-01T23:05Z' -> 'Thursday, October 01 at 7:05 PM' in this machine's local time."""
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return iso
    return when.strftime("%A, %B %d at %-I:%M %p")


def _game_line(event: dict[str, Any], team_id: str) -> str:
    comp = (event.get("competitions") or [{}])[0]
    sides = []
    for c in comp.get("competitors") or []:
        score = c.get("score")
        score = score.get("displayValue") if isinstance(score, dict) else score
        name = (c.get("team") or {}).get("displayName") or "?"
        if c.get("homeAway") in {"home", "away"}:
            name = f"{name} ({c['homeAway']})"
        sides.append((name, score, c.get("winner"), (c.get("team") or {}).get("id") == team_id))
    when = _local(event.get("date") or "")
    status = ((comp.get("status") or {}).get("type") or {})
    if status.get("completed"):
        parts = ", ".join(f"{n} {s}" for n, s, _, _ in sides)
        winner = next((n.split(" (")[0] for n, _, w, _ in sides if w), None)
        return f"Final, played {when}: {parts}" + (f". {winner} won." if winner else ".")
    if status.get("state") == "in":
        parts = ", ".join(f"{n} {s}" for n, s, _, _ in sides)
        return f"In progress now ({status.get('shortDetail') or ''}): {parts}."
    venue = ((comp.get("venue") or {}).get("fullName") or "")
    return f"Scheduled for {when}: " + " vs ".join(n for n, _, _, _ in sides) + (f" at {venue}." if venue else ".")


def facts(question: str, fetch: Fetch = _get) -> list[dict[str, str]]:
    """Search-result-shaped facts for a team's games, or [] (then the web search runs)."""
    try:
        hit = match(question, fetch)
        if hit is None:
            return []
        league, team = hit
        events: list[dict[str, Any]] = []
        for season_type in (2, 3):  # regular season, then postseason
            data = fetch(f"{BASE}/{league}/teams/{team['id']}/schedule?seasontype={season_type}")
            events += data.get("events") or []
        info = (fetch(f"{BASE}/{league}/teams/{team['id']}").get("team") or {})
    except Exception as exc:
        logger.info("speakeasy: scores lookup failed (%s)", type(exc).__name__)
        return []
    events.sort(key=lambda e: e.get("date") or "")
    def state(e):
        return (((e.get("competitions") or [{}])[0].get("status") or {}).get("type") or {})
    finished = [e for e in events if state(e).get("completed")]
    live = [e for e in events if state(e).get("state") == "in"]
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    upcoming = [e for e in [*events, *(info.get("nextEvent") or [])]
                if state(e).get("state") == "pre" and (e.get("date") or "") >= now_iso]
    upcoming.sort(key=lambda e: e.get("date") or "")
    name = team["displayName"]
    out = [{"title": f"{name} ({LEAGUES[league]})", "text": f"Right now it is {datetime.now().astimezone():%A, %B %d, %-I:%M %p} local time."}]
    record = ((info.get("record") or {}).get("items") or [{}])[0].get("summary")
    if record:
        out.append({"title": f"{name} record", "text": f"Record: {record}."})
    for e in live[:1]:
        out.append({"title": f"{name} live", "text": _game_line(e, team["id"])})
    for e in finished[-2:]:
        out.append({"title": f"{name} recent game", "text": _game_line(e, team["id"])})
    seen = set()
    for e in upcoming:
        if e.get("id") in seen:
            continue
        seen.add(e.get("id"))
        out.append({"title": f"{name} next game", "text": _game_line(e, team["id"])})
        break
    if len(out) == 1 or (not finished and not live and len(seen) == 0):
        out.append({"title": f"{name} schedule", "text": "No games found this season (it may be the off-season)."})
    return out
