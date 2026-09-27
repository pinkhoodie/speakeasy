"""'Suggest channels': one read-only Hermes run proposes delivery channels from what it knows.

Hermes gets only the destinations list (labels + targets) and answers strict JSON. Every item is
checked against that list and the settings validator; anything else is dropped. Nothing is saved:
the app shows the suggestions and the user accepts the ones they want.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from .settings import SettingsError, validate_channels

PROMPT_DIR = Path(__file__).parent / "prompt"
SUGGEST_TIMEOUT_S = 90
MAX_SUGGESTIONS = 5
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="speakeasy-suggest")


class SuggestError(Exception):
    pass


def request_prompt(entries: list[dict[str, Any]], threads: bool) -> str:
    listing = "\n".join(f"- {e['target']}: {e.get('label') or e['target']}" for e in entries)
    return ((PROMPT_DIR / "channels_request.md").read_text(encoding="utf-8")
            .replace("{destinations}", listing)
            .replace("{threads}", "true where it helps" if threads else "always false (not supported here)"))


def parse(output: Any, entries: list[dict[str, Any]], threads: bool) -> list[dict[str, Any]]:
    """Validated suggestions: known targets only, each passing the settings validator."""
    if not isinstance(output, str):
        return []
    match = re.search(r"\[.*\]", output, re.S)
    try:
        items = json.loads(match.group(0)) if match else []
    except ValueError:
        return []
    known = {e["target"]: e for e in entries if e.get("target") and e["target"] != "none"}
    picked: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("target") not in known:
            continue
        label = item.get("label") or known[item["target"]].get("label") or item["target"]
        candidate = {"target": item["target"], "label": str(label)[:40].strip(),
                     "topic": str(item.get("topic") or "")[:200].strip(),
                     "new_thread": bool(item.get("new_thread")) and threads}
        try:
            validate_channels(picked + [candidate])
        except SettingsError:
            continue
        picked.append(candidate)
        if len(picked) == MAX_SUGGESTIONS:
            break
    return picked


def suggest(run_fn: Callable[[str, str], tuple[str, str]], entries: list[dict[str, Any]], threads: bool,
            timeout: float = SUGGEST_TIMEOUT_S) -> list[dict[str, Any]]:
    """Ask Hermes once (read-only prompt, ``timeout`` seconds). Raises SuggestError with plain text."""
    usable = [e for e in entries if e.get("target") and e["target"] != "none"]
    if not usable:
        raise SuggestError("No chats are connected to Hermes yet, so there is nothing to suggest.")
    idem = "speakeasy_channels_" + hashlib.sha256(f"{time.time()}".encode()).hexdigest()[:20]
    future = _EXECUTOR.submit(run_fn, request_prompt(usable, threads), idem)
    try:
        status, output = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise SuggestError("Hermes took too long to suggest channels. Try again in a minute.") from None
    except Exception:
        raise SuggestError("Couldn't reach Hermes to suggest channels. Is the gateway running?") from None
    if status != "completed":
        raise SuggestError("Hermes couldn't finish the suggestion. Try again.")
    picked = parse(output, usable, threads)
    if not picked:
        raise SuggestError("Hermes didn't suggest any channels from your chats.")
    return picked
