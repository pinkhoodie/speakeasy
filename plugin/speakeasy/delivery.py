"""Delivery of finished task results and heads-ups to one Hermes chat target (`hermes send`),
plus the destination list the app offers as a picker.

`delivery.target` is ``none`` (default) or a Hermes send target: ``telegram``, ``discord``,
``discord:<chat_id>``, ``telegram:<chat_id>:<thread_id>``, ``slack:#name`` ...
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .settings import valid_delivery_target

logger = logging.getLogger(__name__)
SEND_TIMEOUT_S = 60
INTERNAL_PLATFORMS = {"api_server", "webhook", "voice", "cli", "cron", "acp", "relay"}


def hermes_command() -> list[str]:
    """How to invoke the Hermes CLI from inside the gateway (its own interpreter) or a shell."""
    try:
        import hermes_cli  # type: ignore  # noqa: F401
        return [sys.executable, "-m", "hermes_cli.main"]
    except Exception:
        found = shutil.which("hermes")
        return [found] if found else ["hermes"]


class HermesSendNotifier:
    """Posts text through `hermes send`. The body goes on stdin, never on the command line."""

    def __init__(self, hermes_home: Path, profile: str = "",
                 runner: Callable[..., Any] = subprocess.run):
        self.hermes_home, self.profile, self.runner = Path(hermes_home), profile, runner

    def send(self, target: str, text: str) -> bool:
        if not valid_delivery_target(target) or target == "none" or not text.strip():
            return False
        cmd = [*hermes_command()]
        if self.profile:
            cmd += ["--profile", self.profile]
        cmd += ["send", "--to", target, "--file", "-", "--quiet"]
        env = {**os.environ, "HERMES_HOME": str(self.hermes_home)}
        try:
            done = self.runner(cmd, input=text, text=True, capture_output=True, timeout=SEND_TIMEOUT_S, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("speakeasy: hermes send failed: %s", type(exc).__name__)
            return False
        if getattr(done, "returncode", 1) != 0:
            logger.warning("speakeasy: hermes send exited %s", getattr(done, "returncode", "?"))
            return False
        return True


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _hermes_config(hermes_home: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
        data = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def destinations(hermes_home: Path) -> dict[str, Any]:
    """Connected Hermes platforms, their home channels and known chats (no secrets).

    Sources: gateway_state.json (which platforms are up), config.yaml `platforms.*.home_channel`,
    and channel_directory.json (chats the gateway has seen). Every entry carries a ready-to-use
    `target` string for `delivery.target`.
    """
    hermes_home = Path(hermes_home)
    state = _read_json(hermes_home / "gateway_state.json") or {}
    platform_states = state.get("platforms") if isinstance(state, dict) else None
    platform_states = platform_states if isinstance(platform_states, dict) else {}
    config = _hermes_config(hermes_home)
    configured = config.get("platforms")
    configured = configured if isinstance(configured, dict) else {}
    directory = _read_json(hermes_home / "channel_directory.json") or {}
    chats_by_platform = directory.get("platforms") if isinstance(directory, dict) else None
    chats_by_platform = chats_by_platform if isinstance(chats_by_platform, dict) else {}

    names = (set(platform_states) | set(configured) | set(chats_by_platform)) - INTERNAL_PLATFORMS
    out = []
    for name in sorted(n for n in names if isinstance(n, str)):
        pstate = platform_states.get(name) if isinstance(platform_states.get(name), dict) else {}
        pconf = configured.get(name) if isinstance(configured.get(name), dict) else {}
        home = pconf.get("home_channel") if isinstance(pconf.get("home_channel"), dict) else None
        entry: dict[str, Any] = {
            "platform": name,
            "connected": pstate.get("state") == "connected",
            "state": pstate.get("state") or ("configured" if pconf else "unknown"),
            "target": name,
            "home_channel": None,
            "chats": [],
        }
        if home and home.get("chat_id"):
            target = f"{name}:{home['chat_id']}" + (f":{home['thread_id']}" if home.get("thread_id") else "")
            entry["home_channel"] = {"name": str(home.get("name") or "Home")[:80],
                                     "target": target if valid_delivery_target(target) else name}
        for chat in (chats_by_platform.get(name) or [])[:50]:
            if not isinstance(chat, dict) or not chat.get("id"):
                continue
            chat_id = str(chat["id"])
            target = f"{name}:{chat_id}"
            if not valid_delivery_target(target):
                continue
            entry["chats"].append({"name": str(chat.get("name") or chat_id)[:120], "type": str(chat.get("type") or ""),
                                   "target": target})
        out.append(entry)
    return {"destinations": out, "none": {"target": "none", "name": "Don't post results anywhere"}}


def threads_supported() -> bool:
    """True when the installed Hermes webhook platform can open a new thread per delivery
    (`source_new_thread`). Detected from the installed source, never assumed."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("gateway.platforms.webhook")
        if spec is None or not spec.origin:
            return False
        return "source_new_thread" in Path(spec.origin).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def target_label(target: str) -> str:
    """How the voice prompt names the delivery target ("your Telegram")."""
    if not target or target == "none":
        return ""
    return "your " + target.split(":", 1)[0].title()
