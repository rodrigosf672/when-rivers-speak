"""Process-wide memo for River Pulse results.

``marimo run`` executes every notebook cell for every visitor session, and
sessions share one Python process. The map layers, the discharge matrix and
the packed payloads are identical for everyone, so they are computed once per
process and shared. Keys identify inputs by value where possible and by object
identity for the (themselves memoized) inputs passed between steps.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from typing import Any

_values: dict[Hashable, Any] = {}
_locks: dict[Hashable, threading.Lock] = {}
_guard = threading.Lock()


def memo(key: Hashable, build: Callable[[], Any]) -> Any:
    """Return the value for ``key``, building it once even under concurrent sessions."""
    try:
        return _values[key]
    except KeyError:
        pass
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        if key not in _values:
            _values[key] = build()
        return _values[key]


def clear() -> None:
    """Forget every memoized value (e.g. after rebuilding the layers)."""
    with _guard:
        _values.clear()
        _locks.clear()
