"""Speakeasy: talk to your Hermes agent by voice from a Mac.

Registers the ``voice`` gateway platform and the ``hermes voice`` CLI command.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__version__ = "0.2.0"
__all__ = ["register", "__version__"]

_PLATFORM_HINT = (
    "You are being spoken to through Speakeasy, a voice app on the user's Mac. Your reply is read "
    "aloud: answer in plain spoken sentences, no markdown, tables or long lists."
)


def check_requirements() -> bool:
    return True  # stdlib only


def validate_config(config) -> bool:
    return True


def is_connected(config) -> bool:
    """Configured once `hermes voice setup` has written the voice block (it always sets a port).

    Hermes asks this on an ``enabled=True`` probe view too, so it must key on what setup wrote,
    not on ``enabled``; an explicit ``enabled: false`` in config.yaml still turns voice off.
    """
    extra = getattr(config, "extra", {}) or {}
    return bool(extra.get("port") or extra.get("enabled"))


def register(ctx) -> None:
    from .adapter import VoiceAdapter
    from .cli import handle, setup_parser

    ctx.register_platform(
        name="voice", label="Speakeasy", adapter_factory=lambda cfg: VoiceAdapter(cfg),
        check_fn=check_requirements, validate_config=validate_config, is_connected=is_connected,
        required_env=[], install_hint="No extra packages needed (stdlib only)",
        emoji="\U0001f399", allow_update_command=False, platform_hint=_PLATFORM_HINT,
    )
    ctx.register_cli_command(
        "voice", help="Speakeasy voice: setup, pairing and devices",
        setup_fn=setup_parser, handler_fn=handle,
        description="Set up Speakeasy, pair a Mac, list or revoke paired devices.",
    )
