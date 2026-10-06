"""Which chat a new voice task goes to: the user's opted-in delivery channels, routed by topic.

Order, per new task:
1. The spoken request names a channel ("put this in work", "send it to #research"): that one.
   Two channels named, or a ``#name`` that is not opted in: nothing starts, the voice asks.
2. Otherwise the routing model (``router.decide``: one auxiliary call per handoff) picks the channel
   whose topic fits; any failure or doubt means the default.
3. No match: the default target (``delivery.target``).

Follow-ups never come through here: they stay where their task already runs.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable


@dataclasses.dataclass(frozen=True)
class Channel:
    target: str
    label: str
    topic: str = ""
    new_thread: bool = False
    default: bool = False

    @property
    def key(self) -> str:
        return self.label.lower().lstrip("#")


@dataclasses.dataclass(frozen=True)
class Choice:
    """Where a new task goes. ``channel`` None = no delivery target at all; ``clarify`` set = do not
    start, speak that question instead."""
    channel: Channel | None
    how: str                 # "named" | "topic" | "default" | "clarify"
    clarify: str | None = None


def mode(settings: dict[str, Any]) -> str:
    return settings["delivery"].get("mode") or "home"


def opted_in(settings: dict[str, Any]) -> list[Channel]:
    """The approved channels. In "single" mode there are none: everything goes to the home target."""
    if mode(settings) == "single":
        return []
    return [Channel(c["target"], c["label"], c.get("topic", ""), bool(c.get("new_thread")))
            for c in settings["delivery"].get("channels") or []]


def default_channel(settings: dict[str, Any], label: str) -> Channel | None:
    delivery = settings["delivery"]
    if delivery["target"] == "none":
        return None
    return Channel(delivery["target"], label or delivery["target"], "", bool(delivery.get("new_thread")), default=True)


_HASHTAG = re.compile(r"(?<![\w&])#([A-Za-z0-9][A-Za-z0-9_.-]{0,39})")
_IN_CHANNEL = re.compile(r"(?i)\b(?:in|to|into|over in|on)\s+(?:the\s+|my\s+)?([A-Za-z0-9][A-Za-z0-9_.-]{0,39})"
                         r"\s+(?:channel|chat|thread|group)\b")
_IN_NAME = re.compile(r"(?i)\b(?:start|put|post|do|run|send|drop|open)\b[^.?!]{0,40}?\b(?:in|to|into)\s+"
                      r"(?:the\s+|my\s+)?#?([A-Za-z0-9][A-Za-z0-9_.-]{0,39})\b")


def named(request: str, channels: list[Channel]) -> tuple[list[Channel], list[str]]:
    """Channels the request names explicitly, and names that sound like a channel but are not one.

    A ``#name`` or "in the X channel" is always a channel reference (unknown when not opted in);
    "put this in work" counts only when ``work`` is an opted-in label, so ordinary phrases like
    "put it in writing" never trigger a clarifying question.
    """
    by_key = {c.key: c for c in channels}
    hits: dict[str, Channel] = {}
    unknown: list[str] = []
    for word in [m.group(1) for m in _HASHTAG.finditer(request)] + [m.group(1) for m in _IN_CHANNEL.finditer(request)]:
        key = word.lower().rstrip(".")
        if key in by_key:
            hits[key] = by_key[key]
        elif key not in unknown:
            unknown.append(key)
    for match in _IN_NAME.finditer(request):
        key = match.group(1).lower().rstrip(".")
        if key in by_key:
            hits[key] = by_key[key]
    return list(hits.values()), [u for u in unknown if u not in hits]


def explicit(request: str, settings: dict[str, Any],
             clarify_text: Callable[[list[str], list[str]], str] | None = None) -> Choice | None:
    """The channel the request names outright, a clarifying question, or None (not named)."""
    channels = opted_in(settings)
    if not channels:
        return None
    hits, unknown = named(request, channels)
    # Never stop to ask where something goes: placement should be invisible. Two named channels ->
    # the first one said; a name that isn't a channel ("in the living room", a typo) -> ignored, and
    # the routing model's topical pick or the default decides as usual.
    if hits:
        return Choice(hits[0], "named")
    return None


def resolve(settings: dict[str, Any], default_label: str, picked: str | None) -> Choice:
    """The routing model's topical pick (an opted-in label) or the default target."""
    if mode(settings) != "topic":
        picked = None  # only "sort by topic" sends new work anywhere but home
    key = (picked or "").lower().lstrip("#")
    found = next((c for c in opted_in(settings) if c.key == key), None) if key else None
    return Choice(found, "topic") if found else Choice(default_channel(settings, default_label), "default")


def choose(request: str, settings: dict[str, Any], default_label: str, picked: str | None = None,
           clarify_text: Callable[[list[str], list[str]], str] | None = None) -> Choice:
    """Explicit naming first, then the routing model's topical pick, then the default."""
    return explicit(request, settings, clarify_text) or resolve(settings, default_label, picked)


def _place(target: str) -> tuple[str, str]:
    parts = (target or "").split(":")
    return parts[0].lower(), (parts[1] if len(parts) > 1 else "")


def allows_conversation(settings: dict[str, Any], platform: str, chat_id: str, parent_chat_id: str = "",
                        chat_type: str = "") -> bool:
    """May voice work continue this existing chat? Only when it is the home target or an approved
    channel (or a thread inside one). A bare platform target ("telegram") means its home DM."""
    places = [settings["delivery"]["target"]] + [c.target for c in opted_in(settings)]
    platform = (platform or "").lower()
    for target in places:
        if target == "none":
            continue
        p, chat = _place(target)
        if p != platform:
            continue
        if not chat:
            if chat_type in ("dm", "private", ""):
                return True
            continue
        if chat in (chat_id, parent_chat_id):
            return True
    return False
