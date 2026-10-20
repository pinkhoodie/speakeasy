"""Duck the Mac's system volume while either side of a voice call is speaking.

Speakeasy transcripts drive this from ``calls.handle_event``: input transcript
deltas mean Man is talking, output transcript deltas mean Claire is talking.
Every transcript event lowers the system output volume to a set level; after
``hold_s`` seconds with no transcript on either side, the previous volume comes
back. The saved volume lives in a state file, so a crash, reload or kill while
ducked still restores it on the next import.

Keep this module dependency-free (stdlib only) so it can also be loaded
standalone for testing.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LEVEL = 30.0   # system output volume (%) while either side speaks
DEFAULT_HOLD = 4.0     # seconds of transcript silence before the old volume returns

_GET_VOLUME_RE = re.compile(r"output volume:(\d+).*?output muted:(true|false)")


def _home_dir() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _state_path() -> Path:
    return _home_dir() / "speakeasy" / "duck-state.json"


def _settings() -> dict[str, Any]:
    try:
        return json.loads((_home_dir() / "speakeasy" / "settings.json").read_text())
    except Exception:
        return {}


def _config() -> tuple[bool, float, float]:
    """(enabled, duck level %, hold seconds) from settings.json audio_duck block."""
    if os.environ.get("SPEAKEASY_VOICE_DUCK") == "0":
        return False, DEFAULT_LEVEL, DEFAULT_HOLD
    cfg = _settings().get("audio_duck") or {}
    if cfg.get("enabled") is False:
        return False, DEFAULT_LEVEL, DEFAULT_HOLD
    try:
        level = max(0.0, min(100.0, float(cfg.get("level", DEFAULT_LEVEL))))
    except (TypeError, ValueError):
        level = DEFAULT_LEVEL
    try:
        hold = max(0.5, float(cfg.get("hold_s", DEFAULT_HOLD)))
    except (TypeError, ValueError):
        hold = DEFAULT_HOLD
    return True, level, hold


class _Duck:
    """One duck cycle at a time; a single worker thread keeps osascript calls
    off the gateway's asyncio loop (matches the plugin's Notices._worker pattern)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._jobs: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True, name="speakeasy-duck")
        self._worker.start()
        self._jobs.put(("restore", None))  # crash/reload mid-duck: put the old volume back

    # -- worker ----------------------------------------------------------------------------
    def _run(self) -> None:
        while True:
            op, arg = self._jobs.get()
            try:
                if op == "duck":
                    self._apply_duck(float(arg))
                elif op == "restore":
                    self._apply_restore()
            except Exception as exc:  # never let audio plumbing kill the worker
                logger.warning("speakeasy-duck: %s failed: %s", op, exc)

    @staticmethod
    def _osascript(script: str) -> str | None:
        try:
            proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    def _read_volume(self) -> tuple[int, bool] | None:
        out = self._osascript("get volume settings")
        if not out:
            return None
        match = _GET_VOLUME_RE.search(out.replace("\n", " "))
        if not match:
            return None
        return int(match.group(1)), match.group(2) == "true"

    def _apply_duck(self, level: float) -> None:
        if self._read_state() is not None:
            return  # already ducked; keep the saved level from the first duck
        current = self._read_volume()
        if current is None:
            logger.info("speakeasy-duck: could not read volume; skipping")
            return
        volume, muted = current
        if muted:
            return  # nothing audible to lower; don't track a fake restore
        _state_path().write_text(json.dumps({"volume": volume, "muted": muted}))
        self._osascript(f"set volume output volume {level:g}")
        logger.info("speakeasy-duck: ducked %s -> %s", volume, level)

    def _apply_restore(self) -> None:
        state = self._read_state()
        if state is None:
            return
        try:
            _state_path().unlink()
        except FileNotFoundError:
            pass
        self._osascript(f"set volume output volume {state['volume']}")
        if state.get("muted"):
            self._osascript("set volume output muted true")
        logger.info("speakeasy-duck: restored %s", state["volume"])

    @staticmethod
    def _read_state() -> dict[str, Any] | None:
        try:
            state = json.loads(_state_path().read_text())
        except Exception:
            return None
        if not isinstance(state, dict) or "volume" not in state:
            return None
        return state

    # -- events (called from the asyncio loop; must never block) ----------------------------
    def note(self, speaker: str) -> None:
        enabled, level, hold = _config()
        if not enabled:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._jobs.put(("duck", level))
            self._timer = threading.Timer(hold, self._expire)
            self._timer.daemon = True
            self._timer.start()

    def restore(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._jobs.put(("restore", None))

    def _expire(self) -> None:
        with self._lock:
            self._timer = None
        self._jobs.put(("restore", None))


_DUCK: _Duck | None = None
_DUCK_LOCK = threading.Lock()


def _duck() -> _Duck:
    global _DUCK
    with _DUCK_LOCK:
        if _DUCK is None:
            _DUCK = _Duck()
        return _DUCK


def note(speaker: str) -> None:
    """A transcript delta arrived: speaker is 'user' or 'assistant'."""
    _duck().note(speaker)


def restore() -> None:
    """Put the volume back now (call end, transport close)."""
    _duck().restore()
