"""Which model routes voice requests: a short list of good choices the user can switch between.

Routing is one small, fast decision per request (follow up / split / which chat / show), so it
wants a quick model, not the main agent model. The choice lives in Hermes' own config under
``auxiliary.speakeasy_router`` (Hermes' auxiliary client reads it on every call, so a switch takes
effect on the next request, no restart). Every write goes through ``hermes_config.update``, which
never removes an existing setting.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import hermes_config
from .router import AUX_TASK

VENICE_URL = "https://api.venice.ai/api/v1"

# id -> what gets written under auxiliary.speakeasy_router. Every preset writes every key, so
# switching never leaves a stale base_url or key from the previous choice behind.
_BLANK = {"provider": "auto", "model": "", "base_url": "", "key_env": "", "api_mode": "", "reasoning_effort": ""}
PRESETS: list[dict[str, Any]] = [
    {"id": "default", "label": "Hermes default (your main model)",
     "note": "Uses whatever model Hermes itself runs on. Accurate, but a big model can be slow for routing.",
     "config": dict(_BLANK)},
    {"id": "deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash (Venice)",
     "note": "Fast and cheap. Thinking off: about 1-2 s per request.",
     "needs_env": "VENICE_API_KEY",
     "config": {**_BLANK, "provider": "custom", "model": "deepseek-v4-1-flash", "base_url": VENICE_URL,
                "key_env": "VENICE_API_KEY", "api_mode": "chat_completions", "reasoning_effort": False}},
    {"id": "claude-sonnet-5.5", "label": "Claude Sonnet 5.5 (Anthropic)",
     "note": "Fast and accurate, about 2 s per request; costs more than Flash.",
     "needs_provider": "anthropic",
     "config": {**_BLANK, "provider": "anthropic", "model": "claude-sonnet-5-5"}},
]
_BY_ID = {p["id"]: p for p in PRESETS}


def _config(home: Path) -> dict[str, Any]:
    try:
        return hermes_config._read_full(Path(home) / "config.yaml")
    except hermes_config.ConfigWriteRefused:
        return {}


def _current_block(config: dict[str, Any]) -> dict[str, Any]:
    aux = config.get("auxiliary") if isinstance(config, dict) else None
    block = aux.get(AUX_TASK) if isinstance(aux, dict) else None
    return block if isinstance(block, dict) else {}


def _env_has(home: Path, name: str) -> bool:
    if os.environ.get(name):
        return True
    try:
        for line in (Path(home) / ".env").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name and value.strip():
                return True
    except OSError:
        pass
    return False


def _anthropic_ready(home: Path, config: dict[str, Any]) -> bool:
    model = config.get("model") if isinstance(config, dict) else None
    if isinstance(model, dict) and str(model.get("provider") or "").lower() == "anthropic":
        return True
    return _env_has(home, "ANTHROPIC_API_KEY")


def _available(home: Path, config: dict[str, Any], preset: dict[str, Any]) -> tuple[bool, str]:
    env = preset.get("needs_env")
    if env and not _env_has(home, env):
        return False, f"Needs {env} in your Hermes .env"
    if preset.get("needs_provider") == "anthropic" and not _anthropic_ready(home, config):
        return False, "Needs Anthropic set up in Hermes"
    return True, ""


def _matches(block: dict[str, Any], preset: dict[str, Any]) -> bool:
    want = preset["config"]
    if preset["id"] == "default":
        return str(block.get("provider") or "auto") == "auto" and not str(block.get("model") or "")
    return (str(block.get("provider") or "") == want["provider"]
            and str(block.get("model") or "") == want["model"])


def current(home: Path, config: dict[str, Any] | None = None) -> str:
    """The preset id in use, or "custom" for anything else set by hand."""
    block = _current_block(config if config is not None else _config(home))
    return next((p["id"] for p in PRESETS if _matches(block, p)), "custom")


def choices(home: Path) -> dict[str, Any]:
    config = _config(home)
    block = _current_block(config)
    picked = current(home, config)
    out = []
    for p in PRESETS:
        ok, why = _available(home, config, p)
        out.append({"id": p["id"], "label": p["label"], "note": p["note"], "available": ok, "reason": why})
    custom = ""
    if picked == "custom":
        provider, model = str(block.get("provider") or ""), str(block.get("model") or "")
        custom = f"{provider} · {model}" if model else provider
    return {"current": picked, "custom": custom, "choices": out}


class RoutingChoiceError(ValueError):
    pass


def choose(home: Path, preset_id: str) -> dict[str, Any]:
    """Point auxiliary.speakeasy_router at a preset. Takes effect on the next request."""
    preset = _BY_ID.get((preset_id or "").strip())
    if preset is None:
        raise RoutingChoiceError(f"unknown routing model {preset_id!r}; choose one of: "
                                 + ", ".join(p["id"] for p in PRESETS))
    config = _config(home)
    ok, why = _available(home, config, preset)
    if not ok:
        raise RoutingChoiceError(f"{preset['label']}: {why}")

    def apply(data: dict[str, Any]) -> bool:
        aux = data.setdefault("auxiliary", {})
        if not isinstance(aux, dict):
            return False
        block = aux.setdefault(AUX_TASK, {})
        if not isinstance(block, dict):
            aux[AUX_TASK] = block = {}
        changed = False
        for key, value in preset["config"].items():
            if block.get(key, None) != value:
                block[key] = value
                changed = True
        return changed

    hermes_config.update(Path(home) / "config.yaml", apply)
    return choices(home)
