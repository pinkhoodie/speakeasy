"""Which model routes voice requests, chosen from the providers and models Hermes itself offers.

Routing is one small, fast decision per request (follow up / split / which chat / show). The choice
lives in Hermes' own config under ``auxiliary.speakeasy_router``; Hermes' auxiliary client reads it
on every call, so a switch takes effect on the next request with no restart.

Nothing here names a provider or a model. The providers (and each one's models) come from Hermes'
own model picker: the providers this Hermes is signed in to, the same list ``hermes model`` shows.
Every write goes through ``hermes_config.update``, which never removes an existing setting.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import hermes_config
from .router import AUX_TASK

logger = logging.getLogger(__name__)

DEFAULT = "auto"  # Hermes' own default for an auxiliary task: the main model
# Endpoint keys a previous choice may have set by hand. Switching provider blanks them (never
# deletes them: config writes only ever add), so an old base_url can't hijack the new provider.
_ENDPOINT_KEYS = ("base_url", "api_key", "key_env", "api_mode")
MAX_MODELS = 400


class RoutingChoiceError(ValueError):
    pass


def _config(home: Path) -> dict[str, Any]:
    try:
        return hermes_config._read_full(Path(home) / "config.yaml")
    except hermes_config.ConfigWriteRefused:
        return {}


def _block(config: dict[str, Any]) -> dict[str, Any]:
    aux = config.get("auxiliary") if isinstance(config, dict) else None
    block = aux.get(AUX_TASK) if isinstance(aux, dict) else None
    return block if isinstance(block, dict) else {}


def thinking_on(block: dict[str, Any]) -> bool:
    """Off only when the user turned it off; otherwise the model's own default."""
    effort = block.get("reasoning_effort")
    if effort is None or effort == "":
        return True
    return not (effort is False or str(effort).strip().lower() in {"false", "none", "off", "disabled"})


def current(config: dict[str, Any]) -> dict[str, Any]:
    block = _block(config)
    provider = str(block.get("provider") or DEFAULT).strip() or DEFAULT
    model = str(block.get("model") or "").strip()
    return {"provider": provider, "model": model, "thinking": thinking_on(block),
            "is_default": provider == DEFAULT and not model}


def _hermes_providers(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Hermes' own picker list: signed-in providers with their curated models. Cached catalogs
    only, so a slow provider never stalls Settings."""
    try:
        from hermes_cli.model_switch_providers import list_authenticated_providers  # type: ignore
    except Exception:  # older Hermes without the picker API
        return []
    main = config.get("model")
    main = main if isinstance(main, dict) else {}
    try:
        found = list_authenticated_providers(
            current_provider=str(main.get("provider") or ""), user_providers=config.get("providers"),
            custom_providers=config.get("custom_providers"), max_models=MAX_MODELS,
            non_blocking_catalogs=True, probe_custom_providers=False, for_picker=True)
    except Exception as exc:
        logger.info("speakeasy: could not list Hermes providers: %s", type(exc).__name__)
        return []
    return [{"id": str(p.get("slug") or ""), "name": str(p.get("name") or p.get("slug") or ""),
             "models": [str(m) for m in (p.get("models") or [])][:MAX_MODELS]}
            for p in found if p.get("slug")]


def provider_models(provider: str) -> list[str]:
    """Every model Hermes knows for one provider (its full catalog, not just the curated few)."""
    provider = (provider or "").strip()
    if not provider or provider == DEFAULT:
        return []
    try:
        from hermes_cli.models import provider_model_ids  # type: ignore
        return [str(m) for m in provider_model_ids(provider)][:MAX_MODELS]
    except Exception as exc:
        logger.info("speakeasy: could not list models for %s: %s", provider, type(exc).__name__)
        return []


def label(config: dict[str, Any]) -> str:
    now = current(config)
    if now["is_default"]:
        return "Hermes default (your main model)"
    text = f"{now['provider']} · {now['model']}" if now["model"] else now["provider"]
    return text if now["thinking"] else f"{text} (thinking off)"


def choices(home: Path) -> dict[str, Any]:
    config = _config(home)
    now = current(config)
    providers = _hermes_providers(config)
    # The model in use is always offered, even when the picker's short list leaves it out.
    for p in providers:
        if p["id"] == now["provider"] and now["model"] and now["model"] not in p["models"]:
            p["models"].insert(0, now["model"])
    if not now["is_default"] and not any(p["id"] == now["provider"] for p in providers):
        providers.insert(0, {"id": now["provider"], "name": now["provider"],
                             "models": [now["model"]] if now["model"] else []})
    return {"current": now, "providers": providers, "label": label(config)}


def choose(home: Path, provider: str, model: str = "", thinking: bool | None = None) -> dict[str, Any]:
    """Point auxiliary.speakeasy_router at *provider*/*model*, or back to Hermes' default.
    ``thinking=False`` turns the model's reasoning off (what makes most models fast enough)."""
    provider = (provider or "").strip()
    model = (model or "").strip()
    if not provider:
        raise RoutingChoiceError("provider is required (or 'default')")
    use_default = provider.lower() in {DEFAULT, "default"}
    if not use_default:
        if not model:
            raise RoutingChoiceError("model is required")
        config = _config(home)
        offered = {p["id"] for p in _hermes_providers(config)}
        if offered and provider not in offered and provider != current(config)["provider"]:
            raise RoutingChoiceError(f"Hermes isn't set up for {provider!r}. Signed-in providers: "
                                     + ", ".join(sorted(offered)))

    def apply(data: dict[str, Any]) -> bool:
        aux = data.setdefault("auxiliary", {})
        if not isinstance(aux, dict):
            return False
        block = aux.get(AUX_TASK)
        if not isinstance(block, dict):
            aux[AUX_TASK] = block = {}
        before = dict(block)
        new_provider = DEFAULT if use_default else provider
        switching = str(block.get("provider") or DEFAULT) != new_provider
        block["provider"] = new_provider
        block["model"] = "" if use_default else model
        if switching or use_default:
            for key in _ENDPOINT_KEYS:
                if key in block:
                    block[key] = ""
        if use_default:
            if "reasoning_effort" in block:
                block["reasoning_effort"] = ""
        elif thinking is not None:
            block["reasoning_effort"] = "" if thinking else False
        return block != before

    hermes_config.update(Path(home) / "config.yaml", apply)
    return choices(home)
