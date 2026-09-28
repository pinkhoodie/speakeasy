"""Display/speech sanitizers and the final-answer splitter.

Everything a client or the voice model sees from a Hermes run passes through here: bounded,
secret-looking text rejected or redacted, server-side paths never exposed.
"""
from __future__ import annotations

import ipaddress
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from .cards import LOCAL_IMAGE_EXTS, ImageRejected, vetted_image_url, vetted_local_image

MAX_TRANSCRIPT = 64 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
SETTLED = TERMINAL | {"waiting_for_approval"}
MAX_CARDS = 8
MAX_RESULT_FULL = 8000
AUTHORED_PRECEDENCE_S = 20.0

# Provider non-speech annotations such as "[clear throat]", "[cough]", "[laughs]".
NON_SPEECH_TAG_RE = re.compile(r"\[[A-Za-z][A-Za-z '_-]{0,39}\]")
UNCLOSED_TAG_RE = re.compile(r"\[(?:[A-Za-z][A-Za-z '_-]{0,39})?$")

SENSITIVE_TEXT_RE = re.compile(
    r"(?i)(?:authorization\s*:|bearer\s+|api[_ -]?key\s*[=:]|password\s*[=:]|"
    r"secret\s*[=:]|token\s*[=:]|op:/{2}|sk-[A-Za-z0-9_-]{8,})"
)
GENERIC_SHORT_STATUSES = {
    "request received", "started working", "working on it",
    "using a tool", "searching the web", "running a command", "reading a file",
    "reading a page", "searching files", "delegating a task", "processing request",
}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
HOME_PATH_RE = re.compile(r"(?:~/|/Users/|/home/|/root/|/private/var/)")
URL_SECRET_RE = re.compile(
    r"(?i)[?&#;][^=&\s#]*(?:token|key|sig|auth|code|session|secret|passw|credential)[^=&\s#]*="
)
OPAQUE_RE = re.compile(r"[A-Za-z0-9_+=]{32,}|\b[0-9a-fA-F]{24,}\b")
SECRET_FILE_RE = re.compile(r"(?i)(?:\.env\b|id_rsa|id_ed25519|\.pem\b|device-token|credentials|\.netrc|keychain)")
URL_RE = re.compile(r"https?://[^\s,'\"<>]+", re.IGNORECASE)


def clean_transcript(text: str) -> str:
    """Drop bracketed non-speech tags; keep every real word (mirrors the app)."""
    if "[" not in text:
        return text
    out = NON_SPEECH_TAG_RE.sub(" ", text)
    out = UNCLOSED_TAG_RE.sub("", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.!?;:])", r"\1", out)
    return out.strip()


def safe_user_text(value: Any, limit: int = 500) -> str | None:
    """Bounded display copy, rejecting likely secrets and machine payloads."""
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if not text or SENSITIVE_TEXT_RE.search(text) or "```" in text:
        return None
    return text[:limit]


def valid_short_status(value: Any) -> str | None:
    text = safe_user_text(value, 80)
    if not text or text.lower().rstrip(".!?") in GENERIC_SHORT_STATUSES:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 &'’/.-]*", text):
        return None
    words = re.findall(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*", text)
    if not 2 <= len(words) <= 6:
        return None
    # Active status copy is deliberately narrow: the run authors a present-tense action
    # rather than the server guessing one from a tool.
    if not words[0].lower().endswith("ing"):
        return None
    return text.rstrip(".!?")


def interim_progress(event: dict[str, Any]) -> tuple[str, str] | None:
    """Parse only explicitly user-facing STATUS/DETAIL commentary; never summarize tools."""
    short_status = event.get("short_status")
    detail = event.get("detail")
    text = event.get("text")
    if not isinstance(short_status, str) and isinstance(text, str):
        status_match = re.search(r"(?im)^[ \t]*STATUS:[ \t]*(?P<status>[^\r\n]+?)[ \t]*$", text)
        detail_match = re.search(r"(?im)^[ \t]*DETAIL:[ \t]*(?P<detail>[^\r\n]+?)[ \t]*$", text)
        if status_match:
            short_status = status_match.group("status")
            detail = detail_match.group("detail") if detail_match else short_status
        elif valid_short_status(text):
            short_status, detail = text, text
    safe_status = valid_short_status(short_status)
    safe_detail = safe_user_text(detail, 500)
    if not safe_status or not safe_detail:
        return None
    return safe_status, safe_detail


def _preview_is_unsafe(preview: str) -> bool:
    return bool(SENSITIVE_TEXT_RE.search(preview) or EMAIL_RE.search(preview)
                or URL_SECRET_RE.search(preview) or OPAQUE_RE.search(preview)
                or SECRET_FILE_RE.search(preview) or "```" in preview)


def derive_tool_status(tool: Any, preview: Any) -> tuple[str, str] | None:
    """Honest status from an allowlisted tool's redacted argument preview; None otherwise."""
    if not isinstance(tool, str):
        return None
    text = re.sub(r"\s+", " ", preview).strip() if isinstance(preview, str) else ""
    if text and _preview_is_unsafe(text):
        return None
    if tool in {"read_file", "search_files"}:
        return "Checking project files", "Reading files in the project."
    if tool == "chat_history_lookup":
        detail = safe_user_text(text, 200) if text and not HOME_PATH_RE.search(text) else None
        return "Checking past conversations", detail or "Looking through earlier conversations."
    if not text or HOME_PATH_RE.search(text):
        return None
    if tool == "web_search":
        words = re.findall(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*", text)
        short = valid_short_status("Searching " + " ".join(words[:5])) if words else None
        detail = safe_user_text(text, 200)
        return (short, f"Searching for: {detail}") if short and detail else None
    if tool in {"web_extract", "browser_navigate"}:
        match = URL_RE.search(text)
        if not match:
            return None
        parsed = urllib.parse.urlsplit(match.group(0))
        host = (parsed.hostname or "").lower()
        host = host[4:] if host.startswith("www.") else host
        if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host):
            return None
        short = valid_short_status(f"Reading {host}")
        detail = safe_user_text(f"Reading {host}{parsed.path}"[:200], 200)
        return (short, detail) if short and detail else None
    return None


def safe_full_text(value: Any) -> str | None:
    """Multi-line display copy: keep paragraphs, redact secret-looking lines, bound size."""
    if not isinstance(value, str):
        return None
    lines = []
    for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", line).rstrip()
        lines.append("[redacted]" if SENSITIVE_TEXT_RE.search(line) else line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text[:MAX_RESULT_FULL] or None


def valid_done_label(value: Any) -> str | None:
    """Short past-tense label for the panel's one-line "Done · <label>" status."""
    text = safe_user_text(value, 40)
    if not text or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 &'’/.-]*", text):
        return None
    words = re.findall(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*", text)
    if not 1 <= len(words) <= 5:
        return None
    return text.rstrip(".!?")


def _public_host(url: str, scheme_re: str) -> bool:
    if not re.fullmatch(scheme_re + r"[^\s<>]{4,1500}", url):
        return False
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if (not host or parsed.username or parsed.password or host == "localhost"
            or host.endswith((".local", ".internal", ".test", ".invalid"))):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def product_cards(output: str) -> tuple[str, list[dict[str, Any]]]:
    """Strip an optional `product-cards` payload; keep only bounded, display-safe fields."""
    match = re.search(r"(?s)\n?```product-cards\s*\n(.*?)\n```", output)
    if not match:
        return output, []
    clean = output[:match.start()] + output[match.end():]
    try:
        raw = json.loads(match.group(1))
    except (ValueError, TypeError):
        return clean, []
    if not isinstance(raw, list):
        return clean, []
    cards = []
    for item in raw[:MAX_CARDS]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not _public_host(url, r"https?://"):
            continue
        title = safe_user_text(item.get("name"), 120)
        if not title:
            continue
        card: dict[str, Any] = {"kind": "product", "name": title, "url": url}
        for key, length in (("price", 40), ("store", 60), ("rating", 30), ("image_url", 1500)):
            value = item.get(key)
            if isinstance(value, str):
                value = value.strip()[:length]
                if key == "image_url" and not _public_host(value, r"https://"):
                    continue
                if value:
                    card[key] = value
        specs = item.get("specs")
        if isinstance(specs, list):
            card["specs"] = [s.strip()[:90] for s in specs[:3] if isinstance(s, str) and s.strip()]
        cards.append(card)
    return clean, cards


MEDIA_TAG_RE = re.compile(r"""(?m)(?P<lead>^[ \t]*|[ \t]+)MEDIA:[ \t]*(?:`(?P<tick>[^`\n]+)`|"(?P<quote>[^"\n]+)"|(?P<bare>\S+))[ \t]*$""")
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]\n]{0,120})\]\((https://[^\s)<>]{4,1500})\)")


def media_images(output: str, roots: tuple[Path, ...]) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Image results: Hermes `MEDIA:<path>` tags and markdown images.

    Returns (display text without image MEDIA tags, image cards, every MEDIA tag for chat delivery).
    Local cards keep their path server-side only; `public_result` strips it before any client sees it.
    Paths outside the configured roots, symlinks, and non-image types never become cards.
    """
    cards: list[dict[str, Any]] = []
    tags: list[str] = []
    seen: set[str] = set()
    fences = [(m.start(), m.end()) for m in re.finditer(r"(?s)```.*?```", output)]

    def in_fence(pos: int) -> bool:
        return any(a <= pos < b for a, b in fences)

    spans = []
    for match in MEDIA_TAG_RE.finditer(output):
        if in_fence(match.start()):
            continue
        raw = (match.group("tick") or match.group("quote") or match.group("bare") or "").strip()
        tags.append(raw)
        if Path(raw.split("?", 1)[0]).suffix.lower() not in LOCAL_IMAGE_EXTS:
            continue  # audio/documents keep their tag in the text; they are not image cards
        spans.append((match.start(), match.end()))
        if raw in seen:
            continue
        seen.add(raw)
        name = Path(raw.split("?", 1)[0]).name[:120] or "Image"
        if raw.startswith("https://"):
            try:
                vetted_image_url(raw)
            except ImageRejected:
                continue
            cards.append({"kind": "image", "name": name, "image_url": raw})
        else:
            try:
                path = vetted_local_image(raw, roots)
            except ImageRejected:
                continue
            cards.append({"kind": "image", "name": name, "path": str(path)})
    for match in MARKDOWN_IMAGE_RE.finditer(output):
        if in_fence(match.start()) or match.group(2) in seen:
            continue
        try:
            vetted_image_url(match.group(2))
        except ImageRejected:
            continue
        seen.add(match.group(2))
        title = safe_user_text(match.group(1), 120) or Path(urllib.parse.urlsplit(match.group(2)).path).name or "Image"
        cards.append({"kind": "image", "name": title[:120], "image_url": match.group(2)})
    for start, end in reversed(spans):
        output = output[:start] + output[end:]
    return output, cards, tags


LIVE_MEDIA_RE = re.compile(r"""MEDIA:[ \t]*[`"']?(?P<ref>(?:/|~/|https://)[^\s`"'<>]+)""")
SCREENSHOT_PATH_RE = re.compile(r'"screenshot_path"\s*:\s*"(?P<ref>/[^"\n]{1,1000})"')


def vetted_live_ref(raw: Any, roots: tuple[Path, ...]) -> tuple[str, str, str] | None:
    """One image a running task produced or is looking at -> (kind, ref, name), vetted exactly like
    result cards: local paths only under the image roots, HTTPS only to public hosts."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip().rstrip(".,;)")
    is_url = raw.startswith("https://")
    if Path(urllib.parse.urlsplit(raw).path if is_url else raw).suffix.lower() not in LOCAL_IMAGE_EXTS:
        return None
    try:
        if is_url:
            vetted_image_url(raw)
            return "url", raw, Path(urllib.parse.urlsplit(raw).path).name[:120] or "Image"
        path = vetted_local_image(raw, roots)
    except ImageRejected:
        return None
    return "path", str(path), path.name[:120]


def live_images_in(text: Any, roots: tuple[Path, ...]) -> list[tuple[str, str, str]]:
    """Vetted images named in interim text or a tool result preview: ``MEDIA:<path>`` tags (also
    inside JSON, where the line-anchored tag pattern misses them) and a browser ``screenshot_path``."""
    if not isinstance(text, str) or not text:
        return []
    found: list[tuple[int, str]] = [(m.start(), m.group("tick") or m.group("quote") or m.group("bare") or "")
                                    for m in MEDIA_TAG_RE.finditer(text)]
    found += [(m.start(), m.group("ref")) for m in LIVE_MEDIA_RE.finditer(text)]
    found += [(m.start(), m.group("ref")) for m in SCREENSHOT_PATH_RE.finditer(text)]
    out: list[tuple[str, str, str]] = []
    for _, raw in sorted(found):
        vetted = vetted_live_ref(raw, roots)
        if vetted and vetted not in out:
            out.append(vetted)
    return out


def public_result(result: Any) -> Any:
    """Client view of a stored result: no server-side paths or internal delivery fields."""
    if not isinstance(result, dict):
        return result
    clean = {k: v for k, v in result.items() if not k.startswith("_")}
    if isinstance(clean.get("cards"), list):
        clean["cards"] = [{k: v for k, v in card.items() if k != "path"} if isinstance(card, dict) else card
                          for card in clean["cards"]]
    return clean


def delivery_text(result: dict[str, Any] | None) -> str:
    """Chat delivery: the readable answer plus the original MEDIA tags so the chat still gets images."""
    if not result:
        return ""
    text = (result.get("full") or "").strip()
    missing = [tag for tag in result.get("_media") or [] if isinstance(tag, str) and f"MEDIA:{tag}" not in text]
    return "\n\n".join([text, *(f"MEDIA:{tag}" for tag in missing)]).strip() if missing else text


def split_result(output: Any, image_roots: tuple[Path, ...], fallback_spoken: str = "Done. The details are in the app.") -> dict[str, Any] | None:
    """Split a final answer into the spoken register and the full display register.

    Also strips `product-cards` and `email-draft` fenced payloads (drafts go to `_email_drafts`).
    """
    from .emails import extract_email_drafts

    if not isinstance(output, str) or not output.strip():
        return None
    label = None
    done = list(re.finditer(r"(?im)^[ \t]*DONE:[ \t]*(.+?)[ \t]*$", output))
    if done:
        label = valid_done_label(done[-1].group(1))
        output = output[:done[-1].start()] + output[done[-1].end():]
    spoken_raw = None
    matches = list(re.finditer(r"(?im)^[ \t]*SPOKEN:[ \t]*(.+?)[ \t]*$", output))
    if matches:
        spoken_raw = matches[-1].group(1)
        output = output[:matches[-1].start()] + output[matches[-1].end():]
    output, drafts = extract_email_drafts(output)
    output, cards = product_cards(output)
    output, images, media_tags = media_images(output, image_roots)
    cards = (cards + images)[:MAX_CARDS]
    full = safe_full_text(output)
    spoken = safe_user_text(spoken_raw, 500) if spoken_raw else None
    if not spoken and full:
        sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", full).strip())
        plain = re.sub(r"https?://\S*[^\s.,;:!?)]|[`*_#>]+", "", " ".join(sentences[:2]))
        spoken = safe_user_text(re.sub(r"\s+([.,;:!?])", r"\1", plain), 500)
    if not spoken:
        spoken = fallback_spoken
    result: dict[str, Any] = {"spoken": spoken, "full": full or spoken}
    if cards:
        result["cards"] = cards
    if media_tags:
        result["_media"] = media_tags[:MAX_CARDS]
    if drafts:
        result["_email_drafts"] = drafts
    if label:
        result["label"] = label
    return result


def notice_text(value: Any, limit: int) -> str | None:
    """Chat-safe one-liner: reuse display redaction and drop paths, emails, and opaque tokens."""
    text = safe_user_text(value, 4000)
    if not text or HOME_PATH_RE.search(text) or _preview_is_unsafe(text):
        return None
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def short_title(request: str | None) -> str | None:
    """A to-do style label from the spoken request (no classifier): first few content words."""
    text = notice_text(request, 200)
    if not text:
        return None
    text = re.sub(r"(?i)^(?:(?:hey|ok|okay|so|um+|uh+|please|can you|could you|would you|i need you to|"
                  r"i want you to|go ahead and|let'?s)[\s,]+)+", "", text).strip()
    words = text.rstrip(".!?").split()
    if not words:
        return None
    title = " ".join(words[:6])
    return (title[0].upper() + title[1:])[:60]
