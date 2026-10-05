"""Why a Hermes run failed, in Speakeasy's own words.

Hermes puts the provider's one-line error on a failed run: the ``error`` field of the
``run.failed`` event and of ``GET /v1/runs/{id}``, e.g. ``"HTTP 402: Insufficient USD or Diem
balance"`` (its ``_summarize_api_error``, redacted before it leaves the API server). Only shapes
known to mean one thing are mapped to a fixed sentence, like ``codex_transport.explain_codex_error``;
the provider's own words are never stored, shown, or spoken. They stay in Hermes' errors.log.
"""
from __future__ import annotations

import re
import urllib.error
from pathlib import Path
from typing import Any

# kind -> (short label for the task row, what happened and what to do, for the detail and the voice)
REASONS: dict[str, tuple[str, str]] = {
    "billing": ("Out of credits",
                "Hermes's model provider is out of credits. Top up that account, or run hermes model "
                "on the Hermes machine to switch providers."),
    "auth": ("Provider sign-in rejected",
             "Hermes's model provider rejected its API key or sign-in. Run hermes model on the "
             "Hermes machine to sign in again."),
    "rate_limit": ("Rate-limited",
                   "Hermes's model provider is rate-limiting it. Wait a minute and try again, or run "
                   "hermes model on the Hermes machine to switch providers."),
    "model_not_found": ("Model unavailable",
                        "Hermes's model provider doesn't offer the model Hermes is set to use. Run "
                        "hermes model on the Hermes machine to pick another."),
    # Speakeasy -> Hermes API server (POST /v1/runs), not the model provider. Our own messages.
    "hermes_key": ("Hermes rejected Speakeasy's key",
                   "Hermes's API server rejected Speakeasy's key. Restart Hermes; if that doesn't fix "
                   "it, run hermes voice setup again."),
    "hermes_unreachable": ("Hermes unreachable",
                           "Speakeasy couldn't reach Hermes's API server. Restart Hermes; if that "
                           "doesn't fix it, run hermes voice setup again."),
}
UNKNOWN = "unknown"  # failed with no reason Speakeasy recognizes and no answer: point at the log

# "HTTP 402: ..." is Hermes' own prefix; "Error code: 402 - {...}" is the OpenAI SDK's str(error),
# which Hermes passes on when the error has no parsed body.
_STATUS_RE = re.compile(r"\b(?:http|error code:?|status(?: code)?:?)\s*(\d{3})\b")
# Hermes' error_classifier billing patterns plus Venice's "Insufficient USD or Diem balance".
_BILLING_RE = re.compile(
    r"insufficient\b[^.]{0,40}\b(?:credit|balance|funds|quota)|insufficient_quota|payment required"
    r"|out of credits|credits? (?:have been )?exhausted|credit balance|exceeded your current quota"
    r"|budget limit exceeded|key limit exceeded|spending limit|out of funds|top up your credits"
    r"|no usable credits|requires available credits|account balance is too low|balance_depleted"
    r"|billing hard limit|hard billing limit|billing_not_active")
_MODEL_RE = re.compile(
    r"model_not_found|model not found|no such model|unknown model|invalid model|is not a valid model"
    r"|unsupported model|\bmodel\b[^.\n]{0,80}\b(?:does not exist|not found|is not available|isn't available)")
_RATE_RE = re.compile(r"rate[ _-]?limit|too many requests|provider rate-limited|resource[ _-]?exhausted")
_AUTH_RE = re.compile(
    r"invalid[ _]api[ _]key|incorrect api key|unauthorized|authentication|invalid token|token expired"
    r"|token revoked|not logged in")
# Hermes' _USAGE_LIMIT_PATTERNS / _USAGE_LIMIT_TRANSIENT_SIGNALS: a usage limit with a reset or retry
# window is a periodic quota (wait), not an empty balance (top up).
_USAGE_LIMIT_RE = re.compile(r"usage limit|quota|limit exceeded")
_TRANSIENT_RE = re.compile(r"try again|retry|resets? (?:at|in|after)|available in|wait|requests remaining"
                           r"|periodic|window|per minute|per second")
# A 403 from a CDN/WAF or relay in front of the provider is not the user's key (Hermes'
# _UPSTREAM_BLOCKED_PATTERNS): no reason Speakeasy can act on.
_BLOCK_PAGE_RE = re.compile(r"cloudflare|just a moment|attention required|request was blocked|request blocked"
                            r"|you have been blocked|enable javascript and cookies|cdn-cgi/challenge-platform"
                            r"|cf-browser-verification|__cf_chl|cf-error-details")
# Hermes' fixed lead-ins for the provider's error line in a failed turn's reply (agent/turn_failure_copy.py,
# conversation_loop._billing_terminal_label). Only text after them is read, never the rest of a reply.
_PROVIDER_LINE_RE = re.compile(r"(?:Provider said|Billing or credits exhausted):[^\n]*")


def failure_kind(error: Any) -> str | None:
    """The kind of a Hermes run error, or None when it isn't a shape known to mean one thing.
    402, 403 and 429 follow Hermes' own verdicts (agent/error_classifier.py ``_classify_402``,
    ``_status_403``, ``_status_429``), so the fix the user is told matches what Hermes decided."""
    if not isinstance(error, str) or not error.strip():
        return None
    text = error.lower()
    match = _STATUS_RE.search(text)
    status = int(match.group(1)) if match else None
    transient = bool(_TRANSIENT_RE.search(text))
    if status == 402:
        # "Usage limit reached, try again in 5 minutes" is a periodic quota, not an empty balance.
        return "rate_limit" if _USAGE_LIMIT_RE.search(text) and transient else "billing"
    if status == 429:
        # A quota wall (OpenAI insufficient_quota) is billing only when it isn't itself a rate-limit
        # phrase and names no reset or retry window (Gemini's "exceeded your current quota … retry in 33s").
        wall = _USAGE_LIMIT_RE.search(text) or _BILLING_RE.search(text)
        return "billing" if wall and not _RATE_RE.search(text) and not transient else "rate_limit"
    if status == 403:
        # A spending cap (OpenRouter "Key limit exceeded", xAI "spending limit") is billing; a WAF or
        # relay block page never reached the provider; any other 403 is the key or sign-in.
        if _BILLING_RE.search(text):
            return "billing"
        return None if _BLOCK_PAGE_RE.search(text) else "auth"
    if status == 401:
        return "auth"
    # Any other status (400, 404, …) or none: the words decide.
    if _BILLING_RE.search(text):
        return "billing"
    if _MODEL_RE.search(text):
        return "model_not_found"
    if _RATE_RE.search(text):
        return "rate_limit"
    if _AUTH_RE.search(text):
        return "auth"
    return None


def provider_line(reply: Any) -> str | None:
    """The provider-error line from a failed turn's reply, when Hermes marked one. The session
    chat stream's ``run.failed`` has no ``error`` field; its reply is Hermes' failure message."""
    if not isinstance(reply, str):
        return None
    match = _PROVIDER_LINE_RE.search(reply)
    return match.group(0) if match else None


def start_failure_kind(exc: BaseException) -> str | None:
    """Why Speakeasy couldn't get a run going on Hermes' API server: from the HermesError our client
    raised (its message is ours, never the server's body) or, for the session chat stream, urllib's."""
    from .hermes_api import HermesError
    if isinstance(exc, HermesError):
        if exc.status == 401:
            return "hermes_key"
        return "hermes_unreachable" if exc.message.startswith("Hermes unavailable") else None
    if isinstance(exc, urllib.error.HTTPError):
        return "hermes_key" if exc.code == 401 else None
    if isinstance(exc, (urllib.error.URLError, ConnectionError)):
        return "hermes_unreachable"
    return None


def unknown_text(hermes_home: Path | None) -> str:
    """No recognized reason: say where Hermes keeps the real one (errors.log under its home)."""
    if hermes_home is None:
        return "Hermes didn't say why. Its error log on the Hermes machine has the details."
    log = Path(hermes_home) / "logs" / "errors.log"
    try:
        shown = "~/" + str(log.relative_to(Path.home()))
    except ValueError:
        shown = str(log)
    return f"Hermes didn't say why. Its error log on the Hermes machine has the details: {shown}"


def public_failure(kind: str | None, hermes_home: Path | None = None) -> dict[str, str] | None:
    """What the app shows for a stored kind: ``label`` (row) only for a known reason, ``text`` always."""
    if kind in REASONS:
        label, text = REASONS[kind]
        return {"kind": kind, "label": label, "text": text}
    if kind == UNKNOWN:
        return {"kind": UNKNOWN, "text": unknown_text(hermes_home)}
    return None


def reason_text(kind: str | None) -> str | None:
    """The sentence for a known kind (what the voice says); None for unknown or no kind."""
    return REASONS[kind][1] if kind in REASONS else None


def voice_text(kind: str | None) -> str | None:
    """What the voice's background notes say about a failed task: the known sentence, or for an
    unknown reason a pointer to the log without its path (a file path is no use read aloud)."""
    if kind == UNKNOWN:
        return "Hermes didn't say why; its error log on the Hermes machine has the details."
    return reason_text(kind)
