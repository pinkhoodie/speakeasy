"""``hermes voice`` — setup, pairing, devices, config and status for Speakeasy.

``setup`` touches only this profile: it enables the Hermes API server on loopback (generating
``API_SERVER_KEY`` into the profile ``.env`` only if it is missing, never overwriting values),
enables the ``voice`` platform in ``config.yaml``, creates Speakeasy settings, prints the pairing
link and starts the first voice-brief write.
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
from .settings import (DEFAULT_PORT, Settings, SettingsError, find_codex, get_path, hermes_api_base, hermes_secret,
                       patch_for, read_env_file)


def _home() -> Path:
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home())


def _store(home: Path | None = None) -> DeviceStore:
    return DeviceStore(home or _home())


def setup_parser(parser) -> None:
    sub = parser.add_subparsers(dest="voice_command")
    setup = sub.add_parser("setup", help="Enable Speakeasy for this Hermes profile and pair a Mac")
    setup.add_argument("--server", default="", help="URL the Mac will use (default http://127.0.0.1:<port>)")
    setup.add_argument("--no-open", action="store_true", help="Print the pairing link instead of opening it")
    setup.add_argument("--no-brief", action="store_true", help="Skip the first voice-brief write")
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


def _open(url: str) -> None:
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.run([opener, url], check=False, timeout=10, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


# -- commands -------------------------------------------------------------------------------------

def cmd_setup(args, home: Path) -> int:
    port = voice_port(home)
    added = append_env_if_missing(home / ".env", {
        "API_SERVER_KEY": secrets.token_urlsafe(32),
        "API_SERVER_HOST": "127.0.0.1",
    })
    host = read_env_file(home / ".env").get("API_SERVER_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(f"Note: API_SERVER_HOST is {host}; Speakeasy only talks to it over loopback.")
    for key in added:
        print(f"Added {key} to {home / '.env'}")
    if enable_voice_platform(home, port):
        print("Enabled the voice platform in config.yaml")
    settings = Settings(home)
    settings.ensure()  # creates settings.json (0600) with defaults
    print(f"Settings: {settings.path}")
    binary = find_codex(settings.get()["voice"]["codex_path"])
    if binary is None:
        print("Codex CLI not found. Install it and run `codex login`, or set voice.codex_path.")
    code = _store(home).new_pairing_code()
    server = args.server or f"http://127.0.0.1:{port}"
    link = pairing_link(server, code)
    print(f"Pairing code: {code}  (single use, expires in {PAIR_TTL_S // 60} minutes)")
    print(f"Pairing link: {link}")
    if not args.no_open:
        _open(link)
    print("Restart the Hermes gateway to start the voice server (`hermes gateway restart`).")
    if not args.no_brief:
        started = _start_brief(home)
        print("Asked Hermes to write your voice brief in the background." if started else
              "Voice brief: will be written once the gateway API server is up (`hermes voice config` / the app).")
    return 0


def _start_brief(home: Path) -> bool:
    """Kick off the first brief write if the Hermes API server answers now; otherwise skip."""
    try:
        from .brief import BriefManager
        from .hermes_api import HermesAPI
        settings = Settings(home)
        api = HermesAPI(hermes_api_base(home), lambda: hermes_secret(home, "API_SERVER_KEY"),
                        settings.get()["hermes_profile"])
        if not api.health():
            return False
        manager = BriefManager(home, lambda p, i: api.run_to_completion(p, i, "speakeasy_brief"), settings.get)
        if manager.text():
            return True
        # CLI process exits soon; run it detached through the gateway's API (the run lives there).
        api.start_run(manager.request_prompt(), "speakeasy_brief_setup_" + secrets.token_hex(6), "speakeasy_brief")
        return True
    except Exception:
        return False


def cmd_pair(args, home: Path) -> int:
    code = _store(home).new_pairing_code()
    server = args.server or f"http://127.0.0.1:{voice_port(home)}"
    link = pairing_link(server, code)
    print(f"Pairing code: {code}  (single use, expires in {PAIR_TTL_S // 60} minutes)")
    print(f"Pairing link: {link}")
    if args.send:
        from .delivery import HermesSendNotifier
        from .settings import valid_delivery_target
        if not valid_delivery_target(args.send) or args.send == "none":
            print("--send needs a Hermes send target like telegram or discord:<chat_id>")
            return 2
        ok = HermesSendNotifier(home).send(args.send, f"Pair Speakeasy on your Mac: {link}")
        print("Sent the pairing link." if ok else "Could not send the pairing link.")
        return 0 if ok else 1
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
