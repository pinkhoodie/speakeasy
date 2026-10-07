"""Ambient listening server core (docs/ambient-listening-plan.md).

The Mac owns the microphone: VAD, the wake word and on-device STT. This side keeps the durable
transcript and the compressed working memory, runs the compression cycle through Hermes'
auxiliary (processing) model, and answers Mode B wakes cheaply. Mode A only assembles the seed
for an ephemeral session — it never touches a model.

The LLM is injected as ``llm(prompt, max_tokens) -> str | None`` (VoiceService wires Hermes'
auxiliary client); tests swap in a fake. Anything that would fabricate memory fails loudly
(AmbientError) instead of inventing it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

Llm = Callable[[str, int], "str | None"]

COMPRESS_SYSTEM_NOTE = "You are a conversation compression engine. Output structured, dense summaries."
SUMMARY_SYSTEM_NOTE = "You write dense, specific session summaries in markdown. Skip empty sections."
TITLE_OUTPUT_RULE = 'Output only valid JSON, no other text: {"title": "...", "subtitle": "..."}'


class AmbientError(RuntimeError):
    pass


def compress(store: Any, settings: dict[str, Any], llm: Llm) -> dict[str, Any]:
    """One compression cycle: fold the hot context into the compressed memory through the
    processing model. Every ``summary_every``-th cycle also refreshes the summary and the
    title/subtitle. The counter only advances when compression actually produced text."""
    s = settings["listening"]
    hot = store.ambient_hot(s["hot_context_words"])
    mem = store.ambient_memory_all()
    try:
        count = int(mem.get("compression_count") or 0)
    except ValueError:
        count = 0
    prompt = "\n\n".join([
        COMPRESS_SYSTEM_NOTE,
        "Compress the following conversation context into a structured summary. Maintain: who is in the "
        "room, key topics, decisions, facts, open questions. Be specific and dense; keep names and numbers.",
        f"Target: under {s['compress_target_words']} words.",
        f"Current compressed memory:\n{mem.get('compressed') or '(empty)'}",
        f"New context to integrate:\n{hot or '(nothing new)'}",
    ])
    compressed = (llm(prompt, 1024) or "").strip()
    if not compressed:
        raise AmbientError("the compression model returned nothing")
    store.ambient_memory_set("compressed", compressed)
    count += 1
    store.ambient_memory_set("compression_count", str(count))
    out: dict[str, Any] = {"compressed": compressed, "compression_count": count,
                           "summary": None, "title": None, "subtitle": None}
    if count % s["summary_every"] == 0:
        summary = (llm("\n\n".join([
            SUMMARY_SYSTEM_NOTE,
            "Write a structured markdown session summary. Only include sections that have real content — "
            "skip anything empty or speculative. Choose from: Key Topics, Key Decisions, Action Items, "
            "Key Insights, Open Questions.",
            f"Conversation data:\n{compressed}",
            f"Recent context:\n{hot or '(none)'}",
        ]), 1024) or "").strip()
        if summary:
            store.ambient_memory_set("summary", summary)
            out["summary"] = summary
        title_json = (llm("\n\n".join([
            TITLE_OUTPUT_RULE,
            "Based on this conversation, generate a short session title (2-5 words, no quotes, no "
            "punctuation at the end) and a one-sentence subtitle saying what happened.",
            f"Conversation:\n{compressed}\n{hot}".strip() or "(empty)",
        ]), 256) or "").strip()
        title, subtitle = parse_title_subtitle(title_json)
        if title:
            store.ambient_memory_set("title", title)
            out["title"] = title
        if subtitle:
            store.ambient_memory_set("subtitle", subtitle)
            out["subtitle"] = subtitle
    return out


def parse_title_subtitle(text: str) -> tuple[str | None, str | None]:
    """The title model's JSON object (possibly fenced or padded with prose): title + subtitle."""
    fence = re.search(r"\{.*\}", text or "", re.S)
    if not fence:
        return None, None
    try:
        data = json.loads(fence.group(0))
    except ValueError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    title = str(data.get("title") or "").strip()[:80] or None
    subtitle = str(data.get("subtitle") or "").strip()[:200] or None
    return title, subtitle


def wake(store: Any, settings: dict[str, Any], mode: str, question: str, context_tail: str,
         llm: Llm) -> dict[str, Any]:
    """A wake phrase reached the server.

    Mode B (``insight``): one cheap completion over the ambient memory → ``{"answer"}`` — no
    realtime session, so it stays free of the billed-realtime path.

    Mode A (``question``): no model call — return the seed the Mac opens an ephemeral session
    with, so the session starts already knowing the room and answers the question first.
    """
    s = settings["listening"]
    mem = store.ambient_memory_all()
    compressed = mem.get("compressed") or ""
    if mode == "insight":
        hot = (store.ambient_hot(400).strip() or context_tail.strip())[-1600:]
        prompt = "\n\n".join([
            f"You are {settings.get('assistant_name') or 'the assistant'}. The user just said the wake "
            "phrase with no follow-up: they're inviting you to surface what's been on your mind from the "
            "last few minutes of conversation.",
            "Pull the single most useful thread from the context below and offer it as one short "
            "conversational beat: a fact that matters, a tension between what was said, a question worth "
            "asking, or something missing from the conversation.",
            "Rules: never generic (\"I'm listening\", \"what's up\"); only what's actually in the context; "
            "if there's genuinely nothing yet, say one short phrase admitting it (\"Not much yet, keep "
            "going.\"). Spoken aloud: natural rhythm, contractions, no bullets or markdown, one to three "
            "sentences max.",
            f"Recent conversation:\n{hot or '(nothing heard yet)'}",
            f"Working memory (earlier, compressed):\n{compressed or '(empty — early in the session)'}",
        ])
        answer = (llm(prompt, s["mode_b_max_tokens"]) or "").strip()
        return {"answer": answer or "Not much yet, keep going."}
    hot = store.ambient_hot(s["hot_context_words"])[:8000]
    return {"open_session": True,
            "seed": {"ambient": {"mode": "question", "question": question,
                                 "hot_context": hot or context_tail[:8000],
                                 "compressed": compressed[:6000],
                                 "summary": mem.get("summary") or None}}}
