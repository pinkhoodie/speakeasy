"""Switching the routing model: the Hermes config block it writes, and the HTTP/CLI surfaces."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from speakeasy import routing_choice, router
from fakes import http


def _write(home: Path, config: dict, env: str = "") -> None:
    (home / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (home / ".env").write_text(env, encoding="utf-8")


def _block(home: Path) -> dict:
    return yaml.safe_load((home / "config.yaml").read_text())["auxiliary"][router.AUX_TASK]


BASE = {"model": {"default": "claude-opus-5-5", "provider": "anthropic"},
        "auxiliary": {"compression": {"provider": "custom", "model": "deepseek-v4-1-flash"}},
        "platforms": {"discord": {"enabled": True}}}


def test_switching_writes_one_block_and_keeps_everything_else(tmp_path):
    _write(tmp_path, BASE, "VENICE_API_KEY=x\n")
    assert routing_choice.current(tmp_path) == "default"
    state = routing_choice.choose(tmp_path, "deepseek-v4.1-flash")
    assert state["current"] == "deepseek-v4.1-flash"
    block = _block(tmp_path)
    assert block["model"] == "deepseek-v4-1-flash" and block["base_url"] == routing_choice.VENICE_URL
    assert block["key_env"] == "VENICE_API_KEY"
    assert block["reasoning_effort"] is False  # thinking off: what makes Flash fast enough
    after = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert after["auxiliary"]["compression"] == BASE["auxiliary"]["compression"]
    assert after["platforms"] == BASE["platforms"] and after["model"] == BASE["model"]


def test_switching_away_leaves_no_stale_keys(tmp_path):
    _write(tmp_path, BASE, "VENICE_API_KEY=x\n")
    routing_choice.choose(tmp_path, "deepseek-v4.1-flash")
    routing_choice.choose(tmp_path, "claude-sonnet-5.5")
    block = _block(tmp_path)
    assert block["provider"] == "anthropic" and block["model"] == "claude-sonnet-5-5"
    assert block["base_url"] == "" and block["key_env"] == "" and block["reasoning_effort"] == ""
    routing_choice.choose(tmp_path, "default")
    assert routing_choice.current(tmp_path) == "default"


def test_the_label_names_the_choice(monkeypatch):
    monkeypatch.undo()  # conftest stubs routing_model for other tests
    assert router.routing_model({"auxiliary": {}}) == "Hermes default (your main model)"
    flash = routing_choice.PRESETS[1]["config"]
    assert router.routing_model({"auxiliary": {router.AUX_TASK: dict(flash)}}) == "DeepSeek V4.1 Flash (Venice)"
    assert router.routing_model({"auxiliary": {router.AUX_TASK: {"provider": "x", "model": "y"}}}) == "x · y"


def test_a_choice_that_cannot_work_is_refused_and_nothing_is_written(tmp_path):
    _write(tmp_path, {"model": {"provider": "openai-codex"}}, "")  # no Venice key, no Anthropic
    before = (tmp_path / "config.yaml").read_bytes()
    with pytest.raises(routing_choice.RoutingChoiceError, match="VENICE_API_KEY"):
        routing_choice.choose(tmp_path, "deepseek-v4.1-flash")
    with pytest.raises(routing_choice.RoutingChoiceError, match="Anthropic"):
        routing_choice.choose(tmp_path, "claude-sonnet-5.5")
    with pytest.raises(routing_choice.RoutingChoiceError, match="unknown"):
        routing_choice.choose(tmp_path, "gpt-9")
    assert (tmp_path / "config.yaml").read_bytes() == before
    listed = {c["id"]: c for c in routing_choice.choices(tmp_path)["choices"]}
    assert listed["default"]["available"] and not listed["deepseek-v4.1-flash"]["available"]


def test_a_hand_set_model_shows_as_custom(tmp_path):
    config = dict(BASE, auxiliary={router.AUX_TASK: {"provider": "openrouter", "model": "some/model"}})
    _write(tmp_path, config)
    state = routing_choice.choices(tmp_path)
    assert state["current"] == "custom" and state["custom"] == "openrouter · some/model"


def test_the_app_can_list_and_switch_over_http(server, service, home):
    path = home / "config.yaml"
    cfg = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
    cfg.setdefault("model", {})["provider"] = "anthropic"
    (home / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    base, token = server.base_url, server.token
    status, body = http(base, "GET", "/voice/routing", token=token)
    assert status == 200 and body["current"] == "default"
    status, body = http(base, "POST", "/voice/routing", {"model": "claude-sonnet-5.5"}, token=token)
    assert status == 200 and body["current"] == "claude-sonnet-5.5"
    status, body = http(base, "POST", "/voice/routing", {"model": "nope"}, token=token)
    assert status == 400 and "unknown" in body["error"]
    assert http(base, "GET", "/voice/routing")[0] == 401  # paired devices only
    status, body = http(base, "GET", "/voice/status", token=token)
    assert body["routing_choice"]["current"] == "claude-sonnet-5.5"
