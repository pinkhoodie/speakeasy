"""Duck the Mac's system volume while the assistant is speaking on a call from this Mac.

Off unless ``audio_duck.enabled`` is set. The call's transcript drives it (``SidebandWorker.handle_event``):
an assistant transcript delta on a call whose app runs on this Mac (not an iPhone or another computer)
lowers the output volume to ``audio_duck.level``; after ``audio_duck.hold_s`` seconds with no transcript,
or when the call ends (and no other call is still talking), the previous volume comes back. Output that
is muted, or already at or below the duck level, is left alone.

The volume being replaced is saved in ``<HERMES_HOME>/speakeasy/duck-state.json`` before it is lowered,
and only removed once it has been put back, so a crash, kill or live reload while ducked still restores
it the next time the plugin starts. If the user changes the volume while ducked, their choice wins and
nothing is restored (muting alone is not a change: the level still comes back, unmuting is theirs).

The output device is saved too. If the default output changed while ducked (AirPods connected, speakers
picked), the restore puts the old volume back on the device that was ducked and leaves the new one alone;
when that device can't be reached any more, the duck simply ends.

Settings: ``audio_duck.enabled`` / ``level`` / ``hold_s`` in settings.json (validated in settings.py);
``SPEAKEASY_VOICE_DUCK=0`` in the environment turns it off. macOS only (``osascript``); a no-op
elsewhere. macOS cannot duck other apps alone, so the voice is lowered too.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Protocol

from .settings import DEFAULTS

logger = logging.getLogger(__name__)

STATE_FILE = "duck-state.json"
KILL_SWITCH = "SPEAKEASY_VOICE_DUCK"
# macOS can report a step off from the volume that was set; anything further away means the user moved it.
USER_CHANGE_TOLERANCE = 2
CONFIG_TTL_S = 5.0     # a settings read is reused this long on the transcript hot path
OSASCRIPT_TIMEOUT_S = 2.0
CLOSE_JOIN_S = 5.0     # > one restore (a read and a set) at the osascript timeout
RESTORE_RETRIES = 3    # a restore whose set failed is tried again this many times...
RESTORE_RETRY_S = 2.0  # ...this far apart

_GET_VOLUME_RE = re.compile(r"output volume:(\d+).*?output muted:(true|false)", re.S)


class VolumeBackend(Protocol):
    def read(self) -> tuple[int, bool] | None:
        """(output volume 0-100, muted), or None when it can't be read."""
        ...

    def set_volume(self, volume: int) -> bool:
        """Set the output volume; False when it could not be set."""
        ...

    # Optional: a backend that also knows the output device (``output_device`` -> a stable id, and
    # ``set_device_volume(id, volume)``) lets a restore after a device switch reach the ducked device.


class _CoreAudio:
    """The default output device's UID, and setting one device's volume, through CoreAudio (ctypes)."""

    SYSTEM_OBJECT = 1

    class _Address(ctypes.Structure):
        _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]

    @staticmethod
    def _code(text: str) -> int:
        return int.from_bytes(text.encode("ascii"), "big")

    def __init__(self) -> None:
        self._ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        self._at = ctypes.CDLL("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox")
        self._cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        for fn in (self._ca.AudioObjectGetPropertyData, self._ca.AudioObjectGetPropertyDataSize):
            fn.restype = ctypes.c_int32
        self._at.AudioHardwareServiceSetPropertyData.restype = ctypes.c_int32
        self._cf.CFStringGetCString.restype = ctypes.c_bool
        self._cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        self._cf.CFRelease.argtypes = [ctypes.c_void_p]

    def _addr(self, selector: str, scope: str = "glob") -> "_CoreAudio._Address":
        return self._Address(self._code(selector), self._code(scope), 0)

    def _default_output_id(self) -> int | None:
        dev, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
        err = self._ca.AudioObjectGetPropertyData(ctypes.c_uint32(self.SYSTEM_OBJECT), ctypes.byref(self._addr("dOut")),
                                                  0, None, ctypes.byref(size), ctypes.byref(dev))
        return dev.value if err == 0 and dev.value else None

    def _uid(self, device: int) -> str | None:
        ref, size = ctypes.c_void_p(), ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
        err = self._ca.AudioObjectGetPropertyData(ctypes.c_uint32(device), ctypes.byref(self._addr("uid ")),
                                                  0, None, ctypes.byref(size), ctypes.byref(ref))
        if err != 0 or not ref.value:
            return None
        try:
            buf = ctypes.create_string_buffer(512)
            ok = self._cf.CFStringGetCString(ref, buf, 512, 0x08000100)  # kCFStringEncodingUTF8
            return buf.value.decode() if ok else None
        finally:
            self._cf.CFRelease(ref)

    def _devices(self) -> list[int]:
        addr, size = self._addr("dev#"), ctypes.c_uint32(0)
        if self._ca.AudioObjectGetPropertyDataSize(ctypes.c_uint32(self.SYSTEM_OBJECT), ctypes.byref(addr),
                                                   0, None, ctypes.byref(size)) != 0:
            return []
        ids = (ctypes.c_uint32 * (size.value // 4))()
        if self._ca.AudioObjectGetPropertyData(ctypes.c_uint32(self.SYSTEM_OBJECT), ctypes.byref(addr),
                                               0, None, ctypes.byref(size), ids) != 0:
            return []
        return list(ids)

    def default_output_uid(self) -> str | None:
        device = self._default_output_id()
        return self._uid(device) if device else None

    def set_volume(self, uid: str, volume: int) -> bool:
        device = next((d for d in self._devices() if self._uid(d) == uid), None)
        if device is None:
            return False  # unplugged or gone
        scalar = ctypes.c_float(max(0, min(100, volume)) / 100.0)
        err = self._at.AudioHardwareServiceSetPropertyData(  # kAudioHardwareServiceDeviceProperty_VirtualMainVolume
            ctypes.c_uint32(device), ctypes.byref(self._addr("vmvc", "outp")), 0, None,
            ctypes.c_uint32(ctypes.sizeof(scalar)), ctypes.byref(scalar))
        return err == 0


class OsascriptVolume:
    """The Mac's output volume through ``osascript`` (tens of ms per call: run off the event loop)."""

    @staticmethod
    def _run(script: str) -> str | None:
        try:
            proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                                  timeout=OSASCRIPT_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("speakeasy-duck: osascript failed: %s", type(exc).__name__)
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    def read(self) -> tuple[int, bool] | None:
        match = _GET_VOLUME_RE.search(self._run("get volume settings") or "")
        if not match:
            return None  # e.g. "output volume:missing value" when there is no output device
        return int(match.group(1)), match.group(2) == "true"

    def set_volume(self, volume: int) -> bool:
        return self._run(f"set volume output volume {int(volume)}") is not None

    _core: "_CoreAudio | None | bool" = False  # False = not loaded yet; None = unavailable

    def _coreaudio(self) -> "_CoreAudio | None":
        if self._core is False:
            try:
                self._core = _CoreAudio()
            except (OSError, AttributeError) as exc:
                logger.debug("speakeasy-duck: CoreAudio unavailable: %s", type(exc).__name__)
                self._core = None
        return self._core  # type: ignore[return-value]

    def output_device(self) -> str | None:
        """The default output device's UID (stable across reboots), or None when it can't be read."""
        core = self._coreaudio()
        try:
            return core.default_output_uid() if core else None
        except Exception:  # never let audio plumbing break the call
            return None

    def set_device_volume(self, device: str, volume: int) -> bool:
        """Set one device's volume, whether or not it's the default output now."""
        core = self._coreaudio()
        try:
            return bool(core and core.set_volume(device, volume))
        except Exception:
            return False


def default_backend() -> VolumeBackend | None:
    return OsascriptVolume() if sys.platform == "darwin" else None


def duck_config(settings: dict[str, Any]) -> tuple[bool, int, float]:
    """(enabled, duck level %, hold seconds) from validated settings plus the kill switch."""
    cfg = {**DEFAULTS["audio_duck"], **(settings.get("audio_duck") or {})}
    enabled = cfg["enabled"] is True and os.environ.get(KILL_SWITCH, "").strip() != "0"
    return enabled, int(cfg["level"]), float(cfg["hold_s"])


class Ducker:
    """One duck cycle at a time for one Hermes home.

    ``note()`` and ``restore()`` run on the gateway's asyncio loop and never wait on audio: they move a
    deadline and queue work. One worker thread makes the volume calls and restores once the deadline
    passes (no timer thread per transcript delta). Without a backend everything is a no-op.
    """

    def __init__(self, hermes_home: Path, settings: Callable[[], dict[str, Any]],
                 backend: VolumeBackend | None = None, clock: Callable[[], float] = time.monotonic):
        self.state_path = Path(hermes_home) / "speakeasy" / STATE_FILE
        self._settings = settings
        self._backend = backend
        self._clock = clock
        self._cond = threading.Condition()
        self._ops: deque[tuple[str, int]] = deque()
        self._deadline: float | None = None   # when this cycle restores (None = not ducking)
        self._active: set[str] = set()        # calls that spoke during this cycle
        self._hold = float(DEFAULTS["audio_duck"]["hold_s"])
        self._config: tuple[float, tuple[bool, int, float]] | None = None
        self._closed = False
        self._warned_unreadable = False
        self._restore_retries = 0
        self._worker: threading.Thread | None = None
        if backend is not None:
            self._ops.append(("restore", 0))  # a crash or reload while ducked: put the old volume back
            self._worker = threading.Thread(target=self._run, daemon=True, name="speakeasy-duck")
            self._worker.start()

    # -- events (asyncio loop; never wait on audio) ------------------------------------------------
    def note(self, speaker: str, call: str = "") -> None:
        """A transcript delta arrived on ``call`` (speaker 'user' or 'assistant'): duck, or keep ducking."""
        if self._worker is None:
            return
        with self._cond:
            if self._closed:
                return
            now = self._clock()
            if self._deadline is None:  # a new cycle: settings are read here, not per delta
                enabled, level, hold = self._current_config(now)
                if not enabled:
                    return
                self._hold = hold
                self._ops.append(("duck", level))
            self._active.add(call)
            self._deadline = now + self._hold
            self._cond.notify()

    def restore(self, call: str | None = None) -> None:
        """Put the volume back now: ``call`` ended or paused (None = regardless of other calls).

        While another call that spoke this cycle is still live, its quiet spell decides instead."""
        if self._worker is None:
            return
        with self._cond:
            if self._closed:
                return
            if call is not None:
                self._active.discard(call)
                if self._active and self._deadline is not None:
                    return
            self._end_cycle()
            self._ops.append(("restore", 0))
            self._cond.notify()

    def close(self, timeout: float = CLOSE_JOIN_S) -> None:
        """Restore, then stop the worker (service shutdown or live reload)."""
        if self._worker is None:
            return
        with self._cond:
            if self._closed:
                return
            self._end_cycle()
            self._ops.append(("restore", 0))
            self._closed = True
            self._cond.notify()
        self._worker.join(timeout)
        if self._worker.is_alive():
            logger.warning("speakeasy-duck: worker still busy after %.0fs; the next start restores", timeout)

    def _end_cycle(self) -> None:
        self._deadline = None
        self._active.clear()

    def _current_config(self, now: float) -> tuple[bool, int, float]:
        if self._config is not None and now - self._config[0] < CONFIG_TTL_S:
            return self._config[1]
        try:
            config = duck_config(self._settings())
        except Exception as exc:  # unreadable settings must never break the call
            logger.warning("speakeasy-duck: settings unreadable (%s); not ducking", type(exc).__name__)
            config = (False, 0, 0.0)
        self._config = (now, config)
        return config

    # -- worker ----------------------------------------------------------------------------------
    def _next_op(self) -> tuple[str, int] | None:
        with self._cond:
            while True:
                if self._ops:
                    return self._ops.popleft()
                if self._closed:
                    return None
                if self._deadline is None:
                    self._cond.wait()
                    continue
                remaining = self._deadline - self._clock()
                if remaining <= 0:
                    self._end_cycle()
                    return ("restore", 0)
                self._cond.wait(remaining)

    def _run(self) -> None:
        while (op := self._next_op()) is not None:
            try:
                if op[0] == "duck":
                    self._restore_retries = 0
                    self._apply_duck(op[1])
                elif self._apply_restore():
                    self._restore_retries = 0
                else:
                    self._retry_restore()
            except Exception:  # never let audio plumbing kill the worker
                logger.warning("speakeasy-duck: %s failed", op[0], exc_info=True)

    def _retry_restore(self) -> None:
        """The volume couldn't be put back: try again shortly (a few times), unless a new cycle began."""
        with self._cond:
            if self._closed or self._deadline is not None or self._restore_retries >= RESTORE_RETRIES:
                return
            self._restore_retries += 1
            self._deadline = self._clock() + RESTORE_RETRY_S
            self._cond.notify()

    def _apply_duck(self, level: int) -> None:
        assert self._backend is not None
        if self._read_state() is not None:
            return  # already ducked: keep the volume saved by the first duck
        current = self._backend.read()
        if current is None:
            if not self._warned_unreadable:
                self._warned_unreadable = True
                logger.warning("speakeasy-duck: can't read the Mac's volume (osascript); not ducking")
            return
        volume, muted = current
        if muted or volume <= level:
            return  # nothing to lower, and never raise a quiet Mac
        state: dict[str, Any] = {"volume": volume, "ducked_to": level}
        device = self._output_device()
        if device:
            state["device"] = device
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state))
            os.replace(tmp, self.state_path)  # saved before lowering, so a crash can still restore
        except OSError as exc:
            logger.warning("speakeasy-duck: could not save the volume (%s); not ducking", exc)
            return
        if not self._backend.set_volume(level):
            self._forget_state()  # never lowered: nothing to restore
            return
        logger.debug("speakeasy-duck: ducked %s -> %s", volume, level)

    def _apply_restore(self) -> bool:
        """Put the saved volume back; False only when it should be tried again."""
        assert self._backend is not None
        state = self._read_state()
        if state is None:
            return True
        ducked_device, device = state.get("device"), self._output_device()
        if isinstance(ducked_device, str) and device and device != ducked_device:
            # The output changed while ducked: the old volume goes back on the device that was ducked,
            # and the new one is left as it is. If that device is gone, the duck just ends.
            setter = getattr(self._backend, "set_device_volume", None)
            if setter is None or not setter(ducked_device, state["volume"]):
                logger.debug("speakeasy-duck: the ducked output is gone; ending the duck")
            self._forget_state()
            return True
        current = self._backend.read()
        ducked_to = state.get("ducked_to")
        if current is not None and isinstance(ducked_to, int) and abs(current[0] - ducked_to) > USER_CHANGE_TOLERANCE:
            logger.debug("speakeasy-duck: volume changed while ducked; leaving it")
            self._forget_state()
            return True
        if not self._backend.set_volume(state["volume"]):
            logger.warning("speakeasy-duck: could not restore the volume; will retry")
            return False  # the state file stays: a retry, the next restore or the next start tries again
        self._forget_state()
        logger.debug("speakeasy-duck: restored %s", state["volume"])
        return True

    def _output_device(self) -> str | None:
        reader = getattr(self._backend, "output_device", None)
        if reader is None:
            return None
        try:
            device = reader()
        except Exception:
            return None
        return device if isinstance(device, str) and device else None

    def _forget_state(self) -> None:
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass

    def _read_state(self) -> dict[str, Any] | None:
        try:
            state = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return None
        volume = state.get("volume") if isinstance(state, dict) else None
        if not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= 100:
            return None
        return state
