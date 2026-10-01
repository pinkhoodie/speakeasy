"""Quick answers: a simple public-fact question answered in a few seconds, without the full agent.

One web search through the search provider Hermes is already set up with, then one short answer from
Speakeasy's routing model (``auxiliary.speakeasy_router``, the same fast model the user picked for
routing). Anything the results don't clearly answer comes back as None and goes to Hermes as usual,
so a quick answer is never a guess.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

SEARCH_RESULTS = 5
ANSWER_TIMEOUT_S = 6.0
UNSURE = "UNSURE"
# Never quick, whatever the router said: the user's own things, or doing something.
PERSONAL = re.compile(
    r"(?i)\b(?:my|mine|me|our|i|i'm|i've|we|remind|schedule|book|buy|order|send|email|text|call|message|"
    r"draft|fix|build|deploy|install|delete|cancel|turn|make|open|save|remember|calendar|inbox)\b"
    r"|^\s*(?:please\s+)?(?:set|play|put|start|stop|pause)\b")

# Without Jev, only something that is plainly a question gets a quick try.
QUESTION = re.compile(r"(?i)^(?:(?:hey|so|ok|okay|um|uh|quick question)[,\s]+)*(?:who|what|when|where|which|how|is|are|was|"
                      r"were|does|do|did|can|will|has|have)\b")

SYSTEM = ("Answer a spoken question in one or two short plain sentences, using only these search results. "
          "No lists, links or markdown; it will be read aloud. Say times in the user's local time if you can tell it, "
          "and say plainly when a team didn't play or its season is over. For a game question, \"last night\" or "
          "\"yesterday\" means the most recent finished game unless the dates clearly show it wasn't that recent; "
          "give the final score and who won. If the results don't clearly and currently answer "
          f"it, or they disagree, reply exactly {UNSURE}.")


def eligible(request: str) -> bool:
    text = (request or "").strip()
    return 2 <= len(text.split()) <= 25 and not PERSONAL.search(text)


def search(query: str, limit: int = SEARCH_RESULTS) -> list[dict[str, str]]:
    """Results from Hermes' own configured web search provider (in-process; the plugin runs in the gateway)."""
    try:
        from tools.web_tools import web_search_tool  # type: ignore
    except Exception:
        return []
    try:
        data = json.loads(web_search_tool(query, limit) or "{}")
    except (ValueError, TypeError):
        return []
    web = ((data.get("data") or {}).get("web") or []) if isinstance(data, dict) and data.get("success") else []
    return [{"title": str(w.get("title") or "")[:200], "text": str(w.get("description") or "")[:700]}
            for w in web[:limit] if isinstance(w, dict)]


def answer_messages(question: str, results: list[dict[str, str]], today: str) -> list[dict[str, str]]:
    lines = "\n".join(f"- {r['title']}: {r['text']}" for r in results)
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Today is {today}. Question: {question[:400]}\nResults:\n{lines}"}]


def answer(question: str, today: str, searcher: Callable[[str], list[dict[str, str]]] | None = None,
           writer: Callable[[list[dict[str, str]]], str | None] | None = None) -> str | None:
    """A one-or-two-sentence spoken answer, or None (then Hermes takes it)."""
    if not eligible(question):
        return None
    if searcher is None:
        from . import scores
        results = scores.facts(question) or search(question)
    else:
        results = searcher(question)
    if len(results) < 2:
        return None
    if writer is None:
        from .router import aux_call
        writer = lambda m: aux_call(m, ANSWER_TIMEOUT_S, 160)  # noqa: E731
    try:
        text = writer(answer_messages(question, results, today))
    except Exception as exc:
        logger.info("speakeasy: quick answer unavailable (%s)", type(exc).__name__)
        return None
    text = " ".join(str(text or "").split()).strip()
    if not text or UNSURE in text.upper() or len(text) > 400 or "http" in text or "*" in text:
        return None
    return text
