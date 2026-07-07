"""Lightweight on-disk cache for raw USGS HTTP responses.

Keying is a stable hash of (url, sorted params). Values are written to
``CACHE_DIR`` as plain files so they can be inspected or cleared by hand. The
cache lets repeated fetches (e.g. during development or a re-run of the demo
pipeline) skip the network entirely.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .config import CACHE_DIR


def _key(url: str, params: dict | None) -> str:
    payload = json.dumps({"url": url, "params": params or {}}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _path(url: str, params: dict | None, suffix: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{_key(url, params)}{suffix}"


def get(url: str, params: dict | None, *, max_age_s: float | None = None,
        suffix: str = ".txt") -> str | None:
    """Return cached text for (url, params), or ``None`` on miss/expiry."""
    p = _path(url, params, suffix)
    if not p.exists():
        return None
    if max_age_s is not None and (time.time() - p.stat().st_mtime) > max_age_s:
        return None
    return p.read_text(encoding="utf-8")


def put(url: str, params: dict | None, text: str, *, suffix: str = ".txt") -> Path:
    """Store ``text`` for (url, params) and return the path written."""
    p = _path(url, params, suffix)
    p.write_text(text, encoding="utf-8")
    return p


def clear() -> int:
    """Remove all cached files; return the count removed."""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for f in CACHE_DIR.iterdir():
        if f.is_file():
            f.unlink()
            n += 1
    return n
