"""Fingerprint cache so unchanged layers can be skipped on re-runs."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from geoqa import __version__

logger = logging.getLogger("geoqa.cache")


def fingerprint(
    *,
    source: str,
    file_stat: tuple[float, int] | None,
    config_fragment: dict[str, Any],
    geoqa_version: str | None = None,
) -> str:
    """Return a stable SHA-256 key for a layer + config + tool version."""
    payload = {
        "source": source,
        "mtime": file_stat[0] if file_stat else None,
        "size": file_stat[1] if file_stat else None,
        "config": config_fragment,
        "geoqa": geoqa_version or __version__,
    }
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_stat(path: str | Path) -> tuple[float, int] | None:
    """Return ``(mtime, size)`` for a local file, or ``None`` if unavailable."""
    try:
        p = Path(path)
        if not p.is_file():
            return None
        st = p.stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


def cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def read_entry(cache_dir: Path, key: str) -> dict[str, Any] | None:
    path = cache_path(cache_dir, key)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("ignoring corrupt cache entry %s", path)
        return None


def write_entry(cache_dir: Path, key: str, entry: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, key)
    path.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")


def layer_passed(entry: dict[str, Any] | None) -> bool:
    """True when a cache entry records a clean prior run for this fingerprint."""
    return bool(entry and entry.get("ok") is True)
