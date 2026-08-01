"""Structured counters for DEGRADED-bundle routing — AUTO rate + unverifiable flag counts."""

from __future__ import annotations

from collections import Counter
from threading import Lock
from typing import Any

_LOCK = Lock()
_N = 0
_AUTO = 0
_BY_FLAG: Counter[str] = Counter()


def reset() -> None:
    global _N, _AUTO, _BY_FLAG
    with _LOCK:
        _N = 0
        _AUTO = 0
        _BY_FLAG = Counter()


def record(decision: str, flags: list[str] | None = None) -> None:
    """Record one routed email on a DEGRADED bundle."""
    global _N, _AUTO
    with _LOCK:
        _N += 1
        if decision == "auto":
            _AUTO += 1
        for f in flags or []:
            if str(f).startswith("unverifiable:"):
                _BY_FLAG[str(f)] += 1


def snapshot() -> dict[str, Any]:
    with _LOCK:
        n = _N
        auto = _AUTO
        return {
            "n": n,
            "auto": auto,
            "auto_rate": (auto / n) if n else None,
            "by_flag": dict(_BY_FLAG),
        }
