"""The one way Speakeasy changes a user's Hermes ``config.yaml``.

Hermes' writer treats its argument as the *whole* config: any section left out is deleted from
disk. Speakeasy only ever adds to the config, so every change goes through ``update()``, which
reads the full file, applies the change to that full copy, and refuses to write if the result
would lose anything that was there before. A config that cannot be read is never overwritten.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Callable


class ConfigWriteRefused(RuntimeError):
    """Speakeasy declined to touch config.yaml; the file on disk is unchanged."""


def _read_full(path: Path) -> dict[str, Any]:
    try:
        from hermes_cli.config import read_user_config_raw  # type: ignore
    except ImportError:
        read_user_config_raw = None
    if read_user_config_raw is not None:
        try:
            return read_user_config_raw(path)  # raises instead of returning {} for a broken file
        except Exception as exc:  # noqa: BLE001 - any read failure means "do not write"
            raise ConfigWriteRefused(f"could not read {path.name}: {exc}") from exc
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}
    import yaml  # type: ignore
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigWriteRefused(f"could not read {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigWriteRefused(f"{path.name} is not a settings mapping")
    return data


def _lost_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    """Dotted keys present in *before* but missing from *after* (mappings only)."""
    if not isinstance(before, dict):
        return []
    if not isinstance(after, dict):
        return [prefix or "<root>"]
    lost: list[str] = []
    for key, value in before.items():
        here = f"{prefix}.{key}" if prefix else str(key)
        if key not in after:
            lost.append(here)
        else:
            lost.extend(_lost_paths(value, after[key], here))
    return lost


def _write_full(path: Path, data: dict[str, Any]) -> None:
    try:
        from hermes_cli.config import atomic_config_write  # type: ignore
    except ImportError:
        atomic_config_write = None
    if atomic_config_write is not None:
        atomic_config_write(path, data)  # comment-preserving; *data* is the complete config
        return
    import yaml  # type: ignore
    tmp = path.with_suffix(".yaml.speakeasy.tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)


def update(path: Path, change: Callable[[dict[str, Any]], bool]) -> bool:
    """Apply *change* to a full copy of config.yaml and save it. *change* mutates the dict and
    returns True when it changed something. Returns whether the file was written.

    Raises ``ConfigWriteRefused`` (file untouched) if the config can't be read or if the change
    would remove any existing setting.
    """
    path = Path(path)
    before = _read_full(path)
    after = copy.deepcopy(before)
    if not change(after):
        return False
    lost = _lost_paths(before, after)
    if lost:
        raise ConfigWriteRefused("change would remove existing settings: " + ", ".join(lost[:5]))
    _write_full(path, after)
    written = _read_full(path)
    lost = _lost_paths(before, written)
    if lost:  # should be impossible; say so loudly rather than leave the user guessing
        raise ConfigWriteRefused("config.yaml lost settings while saving: " + ", ".join(lost[:5]))
    return True
