"""``hermes voice`` — setup, pairing, devices, config and status for Speakeasy.

``setup`` touches only this profile: it enables the Hermes API server on loopback (generating
``API_SERVER_KEY`` into the profile ``.env`` only if it is missing, never overwriting values),
enables the ``voice`` platform in ``config.yaml``, sets up voice sign-in, restarts Hermes (asked
first) and waits for the voice server, starts the first voice-brief write and only then opens the
pairing link. See setup_flow.py.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .devices import PAIR_TTL_S, DeviceStore
from .settings import DEFAULT_PORT, Settings, SettingsError, get_path, patch_for, read_env_file


def _home() -> Path:
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home())


def _store(home: Path | None = None) -> DeviceStore:
    return DeviceStore(home or _home())


def setup_parser(parser) -> None:
    sub = parser.add_subparsers(dest="voice_command")
    setup = sub.add_parser("setup", help="Set up Speakeasy for this Hermes profile and pair a Mac")
    setup.add_argument("--server", default="", help="URL the Mac will use (default http://127.0.0.1:<port>)")
    setup.add_argument("--tailscale", action="store_true",
                       help="Also reach the voice server from your other devices over Tailscale (tailnet only)")
    setup.add_argument("--api-key", action="store_true",
                       help="Use an OpenAI API key for voice instead of your ChatGPT sign-in")
    setup.add_argument("--send", default="", help="Send the pairing link to a Hermes chat (e.g. telegram)")
    setup.add_argument("--yes", "-y", action="store_true", help="Answer yes to every question (restart, install)")
    setup.add_argument("--no-restart", action="store_true", help="Don't restart Hermes; print what to do instead")
    setup.add_argument("--no-open", action="store_true", help="Print the pairing link instead of opening it")
    pair = sub.add_parser("pair", help="Show a one-time code to pair a Mac")
    pair.add_argument("--server", default="", help="URL the Mac will use (default http://127.0.0.1:<port>)")
    pair.add_argument("--send", default="", help="Also send the pairing link via `hermes send --to <target>`")
    sub.add_parser("devices", help="List paired devices")
    revoke = sub.add_parser("revoke", help="Revoke a paired device")
    revoke.add_argument("device_id")
    config = sub.add_parser("config", help="Get or set Speakeasy settings")
    config_sub = config.add_subparsers(dest="config_command")
    get = config_sub.add_parser("get", help="Print settings (or one dotted key)")
    get.add_argument("key", nargs="?", default="")
    set_ = config_sub.add_parser("set", help="Set one dotted key, e.g. delivery.target telegram")
    set_.add_argument("key")
    set_.add_argument("value")
    sub.add_parser("status", help="Show voice provider, Hermes API and brief status")


# -- helpers --------------------------------------------------------------------------------------

def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    import yaml  # type: ignore
    tmp = path.with_suffix(".yaml.speakeasy.tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)


def append_env_if_missing(env_path: Path, values: dict[str, str]) -> list[str]:
    """Append KEY=value lines for keys not already present. Never rewrites existing lines."""
    existing = read_env_file(env_path)
    added = [k for k in values if k not in existing]
    if not added:
        return []
    env_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        prefix = "" if not text or text.endswith("\n") else "\n"
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(prefix + "".join(f"{k}={values[k]}\n" for k in added))
    os.chmod(env_path, 0o600)
    return added


def enable_voice_platform(home: Path, port: int) -> bool:
    """Turn on gateway.platforms.voice in this profile's config.yaml. Returns True if changed."""
    path = home / "config.yaml"
    data = _read_yaml(path)
    gateway = data.setdefault("gateway", {}) if isinstance(data.get("gateway", {}), dict) else None
    if gateway is None:
        return False
    platforms = gateway.setdefault("platforms", {})
    voice = platforms.setdefault("voice", {})
    changed = not voice.get("enabled")
    voice["enabled"] = True
    extra = voice.setdefault("extra", {})
    if "port" not in extra:
        extra["port"] = port
        changed = True
    plugins = data.setdefault("plugins", {})
    enabled = plugins.setdefault("enabled", [])
    if isinstance(enabled, list) and "speakeasy" not in enabled:
        enabled.append("speakeasy")
        changed = True
    if changed:
        _write_yaml(path, data)
    return changed


def voice_port(home: Path) -> int:
    data = _read_yaml(home / "config.yaml")
    try:
        return int(data["gateway"]["platforms"]["voice"]["extra"]["port"])
    except (KeyError, TypeError, ValueError):
        return DEFAULT_PORT


def pairing_link(server: str, code: str) -> str:
    return f"speakeasy://pair?server={quote(server, safe='')}&code={code}"


def _hermes_cmd(home: Path) -> list[str]:
    from .delivery import hermes_command
    return hermes_command()


def _open(url: str) -> bool:
    """Open the link; False when nothing handles speakeasy:// (the app isn't installed)."""
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        return subprocess.run([opener, url], check=False, timeout=10, capture_output=True).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# -- commands -------------------------------------------------------------------------------------

def _prepare_profile(home: Path, port: int, out) -> None:
    """Step 1: what Speakeasy needs in this profile. Never overwrites an existing value."""
    added = append_env_if_missing(home / ".env", {
        "API_SERVER_KEY": secrets.token_urlsafe(32),
        "API_SERVER_HOST": "127.0.0.1",
    })
    host = read_env_file(home / ".env").get("API_SERVER_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        out(f"  Note: API_SERVER_HOST is {host}; Speakeasy only talks to it over loopback.")
    if added:
        out("✓ Turned on Hermes' local API (Speakeasy uses it to hand work to your agent)")
    enable_voice_platform(home, port)
    Settings(home).ensure()  # settings.json (0600) with defaults
    out("✓ Speakeasy is enabled in this Hermes profile")


def cmd_setup(args, home: Path, env=None) -> int:
    from . import setup_flow as F
    env = env or F.Env()
    out = env.out
    assume = True if getattr(args, "yes", False) else None
    port = voice_port(home)
    local = f"http://127.0.0.1:{port}"
    out("Setting up Speakeasy…")
    _prepare_profile(home, port, out)

    settings = Settings(home)
    voice_ok = F.voice_sign_in(env, home, settings, api_key=getattr(args, "api_key", False), assume=assume)

    if getattr(args, "no_restart", False):
        up = (env.health or F.voice_health)(local)
        if not up:
            out("• Restart Hermes to start the voice server (`hermes gateway restart`), then run `hermes voice pair`.")
    else:
        up = F.start_voice_server(env, home, local, _hermes_cmd(home), assume=assume)

    server = args.server
    if getattr(args, "tailscale", False):
        server = F.tailscale_url(env, port) or server
    server = server or local

    if up:
        out("✓ Your agent will write a short voice brief in the background: what the voice should know about "
            "you and what it can hand off. You can read and edit it in the app.")

    code = _store(home).new_pairing_code()
    link = pairing_link(server, code)
    out("")
    if not up:
        out(f"Pairing code {code} is ready, but the voice server isn't running yet, so pairing will fail until it is.")
        out(f"Pairing link: {link}")
        return 1
    opened = False
    if not args.no_open and not getattr(args, "send", "") and server.startswith("http://127.0.0.1"):
        opened = _open(link)
    if getattr(args, "send", ""):
        _send_link(home, args.send, link, out)
    if opened:
        out("✓ Opened Speakeasy to pair. Finish setup there (about a minute).")
    else:
        if not getattr(args, "send", ""):
            out("Open this link on the Mac you'll talk from (Speakeasy must be installed there):")
        out(f"  {link}")
    out(f"Or type code {code} in Speakeasy › Connect. It works once and expires in {PAIR_TTL_S // 60} minutes.")
    if not voice_ok:
        out("Voice isn't signed in yet; the app will show how to finish that.")
        return 1
    return 0


def _send_link(home: Path, target: str, link: str, out) -> bool:
    from .delivery import HermesSendNotifier
    from .settings import valid_delivery_target
    if not valid_delivery_target(target) or target == "none":
        out("✗ --send needs a Hermes chat like telegram or discord:<chat_id>")
        return False
    ok = HermesSendNotifier(home).send(target, f"Pair Speakeasy on your Mac: {link}")
    out(f"✓ Sent the pairing link to {target.split(':', 1)[0].title()}. Open it on your Mac." if ok
        else "✗ Couldn't send the pairing link.")
    return ok


def cmd_pair(args, home: Path) -> int:
    code = _store(home).new_pairing_code()
    server = args.server or f"http://127.0.0.1:{voice_port(home)}"
    link = pairing_link(server, code)
    print(f"Pairing code: {code}  (single use, expires in {PAIR_TTL_S // 60} minutes)")
    print(f"Pairing link: {link}")
    if args.send:
        return 0 if _send_link(home, args.send, link, print) else 1
    return 0


def cmd_config(args, home: Path) -> int:
    settings = Settings(home)
    command = getattr(args, "config_command", None)
    if command == "set":
        try:
            updated = settings.patch(patch_for(args.key, args.value))
        except SettingsError as exc:
            print(f"Error: {exc}")
            return 2
        print(json.dumps(get_path(updated, args.key)))
        return 0
    current = settings.get()
    if command == "get" and args.key:
        try:
            print(json.dumps(get_path(current, args.key), indent=2))
        except KeyError:
            print(f"No setting {args.key}")
            return 1
        return 0
    print(json.dumps(current, indent=2))
    return 0


def cmd_status(home: Path) -> int:
    from .service import VoiceService
    service = VoiceService(home, start_threads=False)
    try:
        status = service.status()
    finally:
        service.close()
    for key, value in status.items():
        print(f"{key}: {value}")
    return 0


def handle(args) -> int:
    command = getattr(args, "voice_command", None)
    home = _home()
    if command == "setup":
        return cmd_setup(args, home)
    if command == "pair":
        return cmd_pair(args, home)
    if command == "config":
        return cmd_config(args, home)
    if command == "status":
        return cmd_status(home)
    store = _store(home)
    if command == "devices":
        devices = store.devices()
        if not devices:
            print("No paired devices.")
        for d in devices:
            seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(d.last_seen_at)) if d.last_seen_at else "never"
            print(f"{d.id}  {d.name}  last used {seen}")
        return 0
    if command == "revoke":
        if store.revoke(args.device_id):
            print(f"Revoked {args.device_id}.")
            return 0
        print(f"No device {args.device_id}.")
        return 1
    print("Usage: hermes voice {setup|pair|devices|revoke <id>|config get|set|status}")
    return 2
