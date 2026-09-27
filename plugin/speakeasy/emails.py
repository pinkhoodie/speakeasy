"""Email draft cards: parse Hermes' `email-draft` block, canonicalize, hash.

A task that writes an email for the user ends its answer with a fenced `email-draft` JSON block
instead of sending it. The server strips the block from spoken/visible text, validates it, and
shows it as a card. Nothing is sent until the user presses Approve with the exact hash shown.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

DRAFT_BLOCK_RE = re.compile(r"(?s)\n?```email-draft\s*\n(.*?)\n```")
ADDRESS_RE = re.compile(r"^(?:[^<>\x00-\x1f\"]{0,80}<)?[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)+>?$")
MAX_RECIPIENTS = 50
MAX_SUBJECT = 300
MAX_BODY = 20_000
MAX_DRAFTS_PER_ANSWER = 3
DRAFT_STATES = {"pending", "approved", "denied", "revising", "sent", "failed", "superseded"}


class DraftInvalid(ValueError):
    pass


def _address(value: Any) -> str:
    if not isinstance(value, str):
        raise DraftInvalid("address must be a string")
    text = value.strip()
    if not text or len(text) > 254 or not ADDRESS_RE.fullmatch(text):
        raise DraftInvalid("invalid email address")
    return text


def _addresses(value: Any, field: str, required: bool = False) -> list[str]:
    if value is None:
        value = []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or len(value) > MAX_RECIPIENTS:
        raise DraftInvalid(f"{field} must be a list of addresses")
    out = [_address(v) for v in value]
    if required and not out:
        raise DraftInvalid(f"{field} needs at least one address")
    return out


def normalize_draft(raw: Any) -> dict[str, Any]:
    """Validated canonical draft: from, to, cc, bcc, subject, body (+ optional reply id / account)."""
    if not isinstance(raw, dict):
        raise DraftInvalid("draft must be an object")
    subject = raw.get("subject")
    body = raw.get("body")
    if not isinstance(subject, str) or len(subject) > MAX_SUBJECT or re.search(r"[\r\n\x00]", subject):
        raise DraftInvalid("subject must be one line")
    if not isinstance(body, str) or not body.strip() or len(body) > MAX_BODY or "\x00" in body:
        raise DraftInvalid("body must be plain text")
    if re.search(r"(?is)<\s*(?:html|body|div|p|br|table|script)\b", body):
        raise DraftInvalid("body must be plain text")
    sender = raw.get("from")
    draft: dict[str, Any] = {
        "from": _address(sender) if sender not in (None, "") else "",
        "to": _addresses(raw.get("to"), "to", required=True),
        "cc": _addresses(raw.get("cc"), "cc"),
        "bcc": _addresses(raw.get("bcc"), "bcc"),
        "subject": subject.strip(),
        "body": body.replace("\r\n", "\n").replace("\r", "\n").strip(),
    }
    for key, limit in (("reply_to_message_id", 500), ("account", 200)):
        value = raw.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or len(value) > limit or re.search(r"[\r\n\x00]", value):
            raise DraftInvalid(f"{key} must be one short line")
        draft[key] = value.strip()
    return draft


def canonical_json(draft: dict[str, Any]) -> str:
    return json.dumps(draft, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def draft_sha256(draft: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(draft).encode("utf-8")).hexdigest()


def extract_email_drafts(output: str) -> tuple[str, list[dict[str, Any]]]:
    """Strip every `email-draft` block; return (text, valid canonical drafts). Invalid blocks are
    dropped (and still stripped), never shown half-parsed."""
    drafts: list[dict[str, Any]] = []
    for match in DRAFT_BLOCK_RE.finditer(output):
        try:
            draft = normalize_draft(json.loads(match.group(1)))
        except (ValueError, TypeError):
            continue
        if len(drafts) < MAX_DRAFTS_PER_ANSWER:
            drafts.append(draft)
    return DRAFT_BLOCK_RE.sub("", output), drafts
