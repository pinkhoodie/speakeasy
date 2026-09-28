"""Instant home control (optional, off by default): lights and thermostats straight to Home Assistant.

When ``home_control.enabled`` is on and this Hermes profile already has Home Assistant set up
(Hermes' own ``HASS_URL`` / ``HASS_TOKEN``), a spoken request that is *clearly* one light or
thermostat command ("turn off the kitchen lights", "set the thermostat to 70", "what's it set to")
is answered in about a second by one Home Assistant call, instead of a full Hermes run.

Anything else returns ``None`` from :meth:`HomeControl.respond` and the request goes to Hermes
unchanged: other device kinds (locks, doors, covers, alarms, media, scenes, scripts), compound or
conditional requests, and anything ambiguous (no match, several possible matches, a number that
doesn't parse). Devices come from Home Assistant's own state list (cached briefly) and are matched
by their friendly name or area; nothing is hardcoded.

The token is read per use from the profile's ``.env`` / secret scope and never stored, logged or
spoken. Success is only claimed after Home Assistant answers the service call with HTTP 200.
"""
from __future__ import annotations

import dataclasses
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

# Hermes' own Home Assistant integration reads these (tools/homeassistant_tool.py).
URL_ENV = "HASS_URL"
TOKEN_ENV = "HASS_TOKEN"
DEFAULT_URL = "http://homeassistant.local:8123"

DOMAINS = ("light", "climate")
SERVICES = {"light": {"turn_on", "turn_off"}, "climate": {"set_temperature", "set_hvac_mode"}}
MODES = ("heat", "cool", "off")
CACHE_S = 30.0
TIMEOUT_S = 3.0
LAST_TARGET_S = 300.0
MAX_UTTERANCE = 160

LIGHT_WORDS = {"light", "lights", "lamp", "lamps"}
CLIMATE_WORDS = {"thermostat", "thermostats", "temperature", "heat", "heating", "ac", "a/c", "aircon",
                 "climate", "hvac", "air", "conditioning", "conditioner", "cooling"}
# Words that never name a device: articles, filler, and "the whole house".
FILLER_WORDS = {"the", "my", "our", "a", "in", "on", "of", "for", "at", "to", "please", "up", "down", "room's"}
ALL_WORDS = {"all", "every", "everywhere", "everything", "whole", "house", "entire", "home"}
# Device kinds this add-on never touches: a request naming one always goes to Hermes.
OTHER_KINDS = re.compile(
    r"\b(?:locks?|unlock|doors?|garage|gates?|blinds?|shades?|curtains?|covers?|alarm|security|"
    r"tv|television|music|speakers?|volume|media|scenes?|scripts?|automation|vacuum|fans?|"
    r"plugs?|outlets?|cameras?|sprinklers?|valves?)\b")
NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
NUMBER_WORDS.update({"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
                     "eighty": 80, "ninety": 90, "hundred": 100, "a hundred": 100, "one hundred": 100, "half": 50})

FILLER_PREFIX = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|um+|uh+|and|oh|alright|all right|yeah|please|can you|could you|would you|"
    r"will you|i want you to|i'd like you to|i need you to|go ahead and|just|quickly)[,\s]+)+")
FILLER_SUFFIX = re.compile(r"(?:[,\s]+(?:please|for me|thanks|thank you|now|right now|real quick|quickly))+$")
ACK = re.compile(r"^(?:ok(?:ay)?|yeah|yes|yep|hey|hi|so|um+|uh+|thanks|thank you|alright|great|cool)$")
NOT_OURS = re.compile(
    r"\b(?:and|then|also|except|but|unless|if|when|after|before|until|while|don't|do not|never|not|"
    r"schedule|automation|tomorrow|tonight|every day|timer|remind|minutes?|hours?|o'clock)\b|,")


# -- Home Assistant client ----------------------------------------------------------------------

class HomeAssistantError(Exception):
    """A sanitized failure. ``kind``: auth | unreachable | timeout | http. ``sent``: the service call
    may have run (a timeout after sending), so the outcome is unknown."""

    def __init__(self, kind: str, sent: bool = False, status: int = 0):
        super().__init__(kind)
        self.kind, self.sent, self.status = kind, sent, status


def credentials(hermes_home: Path) -> tuple[str, str] | None:
    """(url, token) from this profile, the way Hermes' Home Assistant tool reads them; None when the
    token is missing. The URL defaults like Hermes does. Values are never logged."""
    from .settings import hermes_secret
    token = hermes_secret(hermes_home, TOKEN_ENV)
    if not token:
        return None
    url = (hermes_secret(hermes_home, URL_ENV) or DEFAULT_URL).rstrip("/")
    if not re.match(r"^https?://[^\s/]+", url):
        return None
    return url, token


class HomeAssistantClient:
    """Minimal REST client: list states, call one allowlisted light/climate service."""

    def __init__(self, url: str, token: str, timeout: float = TIMEOUT_S):
        self._url, self._token, self._timeout = url.rstrip("/"), token, timeout

    def __repr__(self) -> str:  # never expose the token
        return "HomeAssistantClient(<configured>)"

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self._url + "/api/" + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self._token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read(4 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            kind = "auth" if exc.code in (401, 403) else "http"
            raise HomeAssistantError(kind, status=exc.code) from None
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            reason = getattr(exc, "reason", exc)
            timed_out = isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) or "timed out" in str(reason)
            raise HomeAssistantError("timeout" if timed_out else "unreachable", sent=timed_out and method == "POST") from None
        try:
            return json.loads(raw or b"null")
        except ValueError:
            raise HomeAssistantError("http", status=200) from None

    def states(self) -> list[dict[str, Any]]:
        data = self._request("GET", "states")
        return data if isinstance(data, list) else []

    def areas(self, entity_ids: list[str]) -> dict[str, str]:
        """entity_id -> area name through a read-only template render; {} when unavailable."""
        template = "{{ ids | map('area_name') | map('default', '', true) | list | tojson }}"
        try:
            raw = self._request("POST", "template", {"template": template, "variables": {"ids": entity_ids}})
            if isinstance(raw, str):
                raw = json.loads(raw)
        except (HomeAssistantError, ValueError):
            return {}
        if not isinstance(raw, list) or len(raw) != len(entity_ids):
            return {}
        return {e: a for e, a in zip(entity_ids, raw) if isinstance(a, str) and a}

    def call_service(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain not in SERVICES or service not in SERVICES[domain]:
            raise ValueError("service not allowed")
        return self._request("POST", f"services/{domain}/{service}", data)


# -- devices -----------------------------------------------------------------------------------

def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9/']+", (text or "").lower().replace("’", "'"))


@dataclasses.dataclass(frozen=True)
class Device:
    entity_id: str
    name: str
    area: str = ""
    state: str = ""
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict, compare=False, hash=False)

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def name_words(self) -> tuple[str, ...]:
        kind = LIGHT_WORDS if self.domain == "light" else CLIMATE_WORDS
        return tuple(w for w in words(self.name) if w not in kind and w not in FILLER_WORDS)

    @property
    def area_words(self) -> tuple[str, ...]:
        return tuple(w for w in words(self.area) if w not in FILLER_WORDS)

    @property
    def offline(self) -> bool:
        return self.state in {"unavailable", "unknown", ""}


def devices_from_states(states: list[Any], areas: dict[str, str] | None = None) -> list[Device]:
    out = []
    for s in states:
        if not isinstance(s, dict):
            continue
        entity_id = s.get("entity_id")
        if not isinstance(entity_id, str) or not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", entity_id):
            continue
        if entity_id.split(".", 1)[0] not in DOMAINS:
            continue
        raw_attrs = s.get("attributes")
        attrs: dict[str, Any] = raw_attrs if isinstance(raw_attrs, dict) else {}
        friendly = attrs.get("friendly_name")
        name = (friendly.strip() if isinstance(friendly, str) else "") or entity_id.split(".", 1)[1].replace("_", " ")
        out.append(Device(entity_id, name, (areas or {}).get(entity_id, ""), str(s.get("state") or ""), attrs))
    return out


# -- parsing -----------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Intent:
    domain: str                 # light | climate
    op: str                     # on | off | brightness | temperature | mode | query
    target: tuple[str, ...]     # words naming the device/area (empty = unnamed)
    value: float | None = None
    mode: str | None = None
    everything: bool = False    # "all the lights"
    pronoun: bool = False       # "it", "them"


def normalize(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace("—", " ").replace("–", " ")
    t = re.sub(r"(\d)\s*°\s*[fc]?\b", r"\1 degrees", t)
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = re.sub(r"[\"“”]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def command_sentence(text: str) -> str | None:
    """The one command sentence of an utterance (filler removed), or None when there are several."""
    parts = [p.strip(" ,") for p in re.split(r"(?<=[.!?])\s+", normalize(text))]
    parts = [re.sub(r"[.!?]+$", "", p).strip(" ,") for p in parts]
    parts = [p for p in parts if p and not ACK.match(p)]
    if len(parts) != 1:
        return None
    t = FILLER_SUFFIX.sub("", FILLER_PREFIX.sub("", parts[0])).strip(" ,")
    return t or None


def number(text: str) -> float | None:
    text = text.strip().replace("-", " ")
    if re.fullmatch(r"\d{1,3}(?:\.\d)?", text):
        return float(text)
    if text in NUMBER_WORDS:
        return float(NUMBER_WORDS[text])
    parts = text.split()
    if (len(parts) == 2 and NUMBER_WORDS.get(parts[0], 0) in range(20, 100, 10)
            and NUMBER_WORDS.get(parts[1], 0) in range(1, 10)):
        return float(NUMBER_WORDS[parts[0]] + NUMBER_WORDS[parts[1]])
    return None


NUM = r"(\d{1,3}(?:\.\d)?|[a-z]+(?:[ -](?!percent\b|degrees?\b|brightness\b)[a-z]+)?)"


def _kind(ws: list[str]) -> str | None:
    has_light = any(w in LIGHT_WORDS for w in ws)
    has_climate = any(w in CLIMATE_WORDS for w in ws)
    if has_light == has_climate:
        return None
    return "light" if has_light else "climate"


def _target(phrase: str, domain: str) -> tuple[tuple[str, ...], bool, bool] | None:
    """(target words, everything, pronoun) from a target phrase, or None when it names something
    that isn't this domain."""
    ws = words(phrase)
    if ws in (["it"], ["them"], ["that"], ["those"]):
        return (), False, True
    kind = LIGHT_WORDS if domain == "light" else CLIMATE_WORDS
    other = CLIMATE_WORDS if domain == "light" else LIGHT_WORDS
    if any(w in other for w in ws):
        return None
    everything = any(w in ALL_WORDS for w in ws)
    if everything and domain == "climate":
        return None  # several thermostats at once: Hermes handles it
    rest = tuple(w for w in ws if w not in kind and w not in FILLER_WORDS and w not in ALL_WORDS)
    return rest, everything, False


def parse(utterance: str) -> Intent | None:
    """One light/thermostat intent from a spoken request, else None (not ours, or not clear)."""
    t = command_sentence(utterance)
    if t is None or len(t) > MAX_UTTERANCE or NOT_OURS.search(t) or OTHER_KINDS.search(t):
        return None
    t = t.replace("air conditioning", "ac").replace("air conditioner", "ac").replace("a/c", "ac")
    ws = words(t)
    # Questions: "what's the thermostat set to", "what's it set to", "what is the bedroom at".
    m = re.fullmatch(r"(?:what(?:'s| is)|what temperature is|where(?:'s| is)) (.+?) (?:set to|set at|at|on)", t)
    if m:
        target = _target(m[1], "climate")
        if target is None or (not target[2] and _kind(words(m[1])) != "climate" and not target[0]):
            return None
        return Intent("climate", "query", target[0], everything=False, pronoun=target[2])
    if t.startswith(("what", "how", "is ", "are ", "why", "who", "when", "where")):
        return None
    kind = _kind(ws)
    # Thermostat mode: "set the thermostat to heat", "switch the ac to cool".
    m = re.fullmatch(r"(?:set|switch|put|change|turn) (.+?) (?:to|on|into) (heat|heating|cool|cooling|off)(?: mode)?", t)
    if m and (kind == "climate" or _kind(words(m[1])) == "climate"):
        target = _target(m[1], "climate")
        if target is None:
            return None
        mode = {"heating": "heat", "cooling": "cool"}.get(m[2], m[2])
        return Intent("climate", "mode", target[0], mode=mode, pronoun=target[2])
    # Set a number: temperature (degrees, or a thermostat word) or brightness (percent, or a light word).
    m = re.fullmatch(r"(?:set|put|make|change|turn|dim|bring|adjust) (.+?) (?:brightness |temperature )?(?:to|at) "
                     + NUM + r"(?: (percent|degrees?(?: fahrenheit| celsius)?))?(?: brightness)?", t)
    if m:
        value = number(m[2])
        unit = m[3] or ""
        if value is None:
            return None
        phrase_kind = _kind(words(m[1]))
        domain = ("climate" if unit.startswith("degree") else "light" if unit == "percent" or t.startswith("dim")
                  else phrase_kind)
        if domain is None or (phrase_kind and phrase_kind != domain):
            return None
        target = _target(m[1], domain)
        if target is None:
            return None
        if domain == "light":
            return Intent("light", "brightness", target[0], value, everything=target[1], pronoun=target[2])
        return Intent("climate", "temperature", target[0], value, pronoun=target[2])
    # Power: "turn off the kitchen lights", "turn the lamp on", "kill the lights", "lights off".
    power = _power(t)
    if power is None:
        return None
    op, phrase = power
    phrase_kind = _kind(words(phrase))
    if phrase_kind == "climate":
        cw = set(words(phrase))
        if op == "off":
            mode = "off"
        elif cw & {"heat", "heating"}:
            mode = "heat"
        elif cw & {"ac", "cooling", "aircon"}:
            mode = "cool"
        else:
            return None  # "turn on the thermostat": which mode? Hermes asks.
        target = _target(phrase, "climate")
        if target is None:
            return None
        return Intent("climate", "mode", target[0], mode=mode, pronoun=target[2])
    target = _target(phrase, "light")
    if target is None or (phrase_kind != "light" and not target[2]):
        return None
    if target[1] and op != "off":
        return None  # only "all lights off" runs across the whole house
    return Intent("light", op, target[0], everything=target[1], pronoun=target[2])


def _power(t: str) -> tuple[str, str] | None:
    """(on|off, target phrase) for a power command, else None."""
    m = re.fullmatch(r"(?:turn|switch|shut|power|flip) (on|off) (.+)", t)
    if m:
        return m[1], m[2]
    m = re.fullmatch(r"(?:turn|switch|shut|power|flip) (.+) (on|off)( everywhere)?", t) \
        or re.fullmatch(r"(.+?) (on|off)( everywhere)?", t)
    if m:
        return m[2], m[1] + (m[3] or "")
    m = re.fullmatch(r"(?:kill|cut) (.+)", t)
    return ("off", m[1]) if m else None


# -- resolving and running ---------------------------------------------------------------------

@dataclasses.dataclass
class Reply:
    ok: bool
    spoken: str
    entity_ids: tuple[str, ...] = ()


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _title(text: str) -> str:
    return text[:1].upper() + text[1:]


def match(target: tuple[str, ...], devices: list[Device]) -> tuple[list[Device], str] | None:
    """Devices a spoken target means, with how to say them; None when nothing or too much matches.

    Order: an area named exactly ("kitchen" = every light in the Kitchen area), a device named
    exactly, then devices whose names start with the words ("kitchen" = Kitchen Ceiling, Kitchen
    Island), then a single device containing all the words. Anything else is ambiguous."""
    if not target:
        return None
    want = set(target)
    by_area = [d for d in devices if d.area_words and set(d.area_words) == want]
    if by_area:
        return by_area, by_area[0].area
    exact = [d for d in devices if set(d.name_words) == want]
    if len(exact) == 1:
        return exact, exact[0].name
    if exact:
        return None
    prefix = [d for d in devices if d.name_words[:len(target)] == target]
    if prefix:
        return prefix, " ".join(target) if len(prefix) > 1 else prefix[0].name
    contains = [d for d in devices if want <= set(d.name_words) | set(d.area_words)]
    if len(contains) == 1:
        return contains, contains[0].name
    return None


class HomeControl:
    """The fast path. ``respond`` returns a spoken Reply, or None to hand the request to Hermes."""

    def __init__(self, enabled: Callable[[], bool], creds: Callable[[], tuple[str, str] | None],
                 client_factory: Callable[[str, str], Any] = HomeAssistantClient,
                 clock: Callable[[], float] = time.monotonic, cache_s: float = CACHE_S):
        self._enabled, self._creds, self._client_factory = enabled, creds, client_factory
        self._clock, self._cache_s = clock, cache_s
        self._lock = threading.Lock()
        self._cache: tuple[float, str, list[Device]] | None = None
        self.last_climate: str | None = None           # entity id of the thermostat used last
        self._last: tuple[float, tuple[str, ...]] | None = None  # "it": the last target

    def active(self) -> bool:
        try:
            return bool(self._enabled()) and self._creds() is not None
        except Exception:
            return False

    def _client(self) -> Any:
        creds = self._creds()
        if creds is None:
            raise HomeAssistantError("auth")
        return self._client_factory(*creds)

    def devices(self, client: Any) -> list[Device]:
        now = self._clock()
        creds = self._creds()
        key = creds[0] if creds else ""
        cached = self._cache
        if cached and cached[1] == key and now - cached[0] < self._cache_s:
            return cached[2]
        states = client.states()
        ids = [s.get("entity_id") for s in states if isinstance(s, dict)
               and str(s.get("entity_id", "")).split(".", 1)[0] in DOMAINS]
        areas = client.areas(ids) if ids and hasattr(client, "areas") else {}
        found = devices_from_states(states, areas)
        self._cache = (now, key, found)
        return found

    def respond(self, utterance: str, assistant_name: str = "Hermes") -> Reply | None:
        """Handle one spoken request. None = not a clear light/thermostat command (use Hermes)."""
        if not self.active():
            return None
        intent = parse(utterance)
        if intent is None:
            return None
        with self._lock:
            try:
                client = self._client()
                devices = self.devices(client)
                plan = self._resolve(intent, devices)
                if plan is None:
                    return None
                if isinstance(plan, Reply):
                    return plan
                return self._run(client, intent, *plan)
            except HomeAssistantError as exc:
                return self._failure(exc, assistant_name)

    @staticmethod
    def _failure(exc: HomeAssistantError, assistant_name: str) -> Reply:
        offer = f" Want me to hand it to {assistant_name} instead?"
        if exc.kind == "auth":
            return Reply(False, "Home Assistant turned me down; its access token isn't working, so nothing changed." + offer)
        if exc.sent:
            return Reply(False, "Home Assistant didn't answer in time, so I can't confirm it changed." + offer)
        if exc.kind in {"unreachable", "timeout"}:
            return Reply(False, "I couldn't reach Home Assistant, so nothing changed." + offer)
        return Reply(False, "Home Assistant didn't accept that, so nothing changed." + offer)

    def _resolve(self, intent: Intent, devices: list[Device]) -> tuple[list[Device], str] | Reply | None:
        pool = [d for d in devices if d.domain == intent.domain]
        if not pool:
            return None
        if intent.pronoun:
            last = self._last
            if not last or self._clock() - last[0] > LAST_TARGET_S:
                if intent.domain == "climate" and self.last_climate:
                    last = (self._clock(), (self.last_climate,))
                else:
                    return None
            chosen = [d for d in pool if d.entity_id in last[1]]
            if not chosen or len(chosen) != len(last[1]):
                return None
            return chosen, _join([d.name for d in chosen]) if len(chosen) <= 2 else "those"
        if intent.domain == "light" and intent.everything and not intent.target:
            if intent.op != "off":
                return None
            return pool, "all the lights"
        if intent.domain == "climate" and not intent.target:
            if len(pool) == 1:
                return pool, pool[0].name
            last = next((d for d in pool if d.entity_id == self.last_climate), None)
            return ([last], last.name) if last else None
        found = match(intent.target, pool)
        if found is None:
            return None
        chosen, label = found
        if intent.domain == "climate" and len(chosen) != 1:
            return None  # several thermostats: Hermes asks which
        return chosen, label

    def _run(self, client: Any, intent: Intent, chosen: list[Device], label: str) -> Reply:
        live = [d for d in chosen if not d.offline]
        if not live:
            verb = "is" if len(chosen) == 1 else "are"
            return Reply(False, f"{_title(label)} {verb} offline in Home Assistant, so I didn't send anything.")
        ids = [d.entity_id for d in live]
        target: Any = ids[0] if len(ids) == 1 else ids
        spoken_label = label if label == "all the lights" else _title(label)
        if intent.domain == "light":
            lights = spoken_label if label == "all the lights" or any(w in LIGHT_WORDS for w in words(label)) \
                else f"{spoken_label} {'lights' if len(live) > 1 else 'light'}"
            lights = _title(lights)
            if intent.op == "brightness":
                pct = intent.value
                if pct is None or not 0 <= pct <= 100:
                    return Reply(False, "Brightness goes from 0 to 100 percent.")
                pct = int(round(pct))
                if pct == 0:
                    client.call_service("light", "turn_off", {"entity_id": target})
                    spoken = f"{lights} off."
                else:
                    client.call_service("light", "turn_on", {"entity_id": target, "brightness_pct": pct})
                    spoken = f"{lights} at {pct} percent."
            else:
                client.call_service("light", "turn_" + intent.op, {"entity_id": target})
                spoken = f"{lights} {intent.op}."
            self._last = (self._clock(), tuple(ids))
            self._cache = None  # the next request sees the new state
            return Reply(True, spoken, tuple(ids))
        device = live[0]
        attrs = device.attributes
        name = _title(device.name)
        self.last_climate = device.entity_id
        self._last = (self._clock(), (device.entity_id,))
        if intent.op == "query":
            return Reply(True, _describe_climate(device), (device.entity_id,))
        if intent.op == "mode":
            modes = attrs.get("hvac_modes")
            if isinstance(modes, list) and intent.mode not in modes:
                return Reply(False, f"{name} doesn't have a {intent.mode} mode.")
            client.call_service("climate", "set_hvac_mode", {"entity_id": device.entity_id, "hvac_mode": intent.mode})
            self._cache = None
            return Reply(True, f"{name} off." if intent.mode == "off" else f"{name} set to {intent.mode}.",
                         (device.entity_id,))
        value = intent.value
        low, high = attrs.get("min_temp"), attrs.get("max_temp")
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
            low, high = (7, 35) if _celsius(attrs) else (45, 95)
        if value is None or not low <= value <= high:
            return Reply(False, f"{name} goes from {_num(low)} to {_num(high)} degrees, so I left it alone.")
        if device.state == "off":
            return Reply(False, f"{name} is off. Say heat or cool first, then the temperature.")
        if not isinstance(attrs.get("temperature"), (int, float)) and (
                isinstance(attrs.get("target_temp_low"), (int, float)) or isinstance(attrs.get("target_temp_high"), (int, float))):
            return Reply(False, f"{name} is in a range mode with two set points, so I left it alone.")
        client.call_service("climate", "set_temperature", {"entity_id": device.entity_id, "temperature": value})
        self._cache = None
        return Reply(True, f"{name} set to {_num(value)}.", (device.entity_id,))


def _celsius(attrs: dict[str, Any]) -> bool:
    high = attrs.get("max_temp")
    return isinstance(high, (int, float)) and high <= 50


def _num(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def _describe_climate(device: Device) -> str:
    a = device.attributes
    name = _title(device.name)
    if device.offline:
        return f"{name} is offline in Home Assistant."
    if device.state == "off":
        text = f"{name} is off."
    elif isinstance(a.get("temperature"), (int, float)):
        text = f"{name} is set to {_num(a['temperature'])}, on {device.state.replace('_', ' ')}."
    elif isinstance(a.get("target_temp_low"), (int, float)) and isinstance(a.get("target_temp_high"), (int, float)):
        text = f"{name} is set between {_num(a['target_temp_low'])} and {_num(a['target_temp_high'])}."
    else:
        text = f"{name} is on {device.state.replace('_', ' ')}."
    if isinstance(a.get("current_temperature"), (int, float)):
        text += f" It's {_num(a['current_temperature'])} there now."
    return text


# -- enabling ----------------------------------------------------------------------------------

def enable(hermes_home: Path, on: bool = True, check: Callable[[tuple[str, str]], str | None] | None = None) -> tuple[bool, str]:
    """Turn the add-on on (only when Hermes already has Home Assistant set up) or off.
    Writes only Speakeasy's own settings; never config.yaml or .env."""
    from .settings import Settings
    settings = Settings(hermes_home)
    if not on:
        settings.patch({"home_control": {"enabled": False}})
        return True, "Instant home control is off. Light and thermostat requests go to Hermes as usual."
    creds = credentials(hermes_home)
    if creds is None:
        return False, (f"Home Assistant isn't set up in Hermes yet ({TOKEN_ENV} is missing), so instant home "
                       "control stays off. Set up Hermes' Home Assistant integration first (`hermes setup`), then "
                       "run this again.")
    problem = check(creds) if check else None
    if problem:
        return False, f"Instant home control stays off: {problem}"
    settings.patch({"home_control": {"enabled": True}})
    return True, ("Instant home control is on. During a call, simple light and thermostat requests go straight "
                  "to Home Assistant; everything else still goes to Hermes. Takes effect on the next request.")


def check_connection(creds: tuple[str, str], client_factory: Callable[[str, str], Any] = HomeAssistantClient) -> str | None:
    """None when Home Assistant answers the state list (read-only); else a short reason."""
    try:
        states = client_factory(*creds).states()
    except HomeAssistantError as exc:
        return {"auth": "Home Assistant rejected the access token.",
                "timeout": "Home Assistant didn't answer in time."}.get(exc.kind, "couldn't reach Home Assistant.")
    if not any(str(s.get("entity_id", "")).split(".", 1)[0] in DOMAINS for s in states if isinstance(s, dict)):
        return "Home Assistant has no lights or thermostats."
    return None
