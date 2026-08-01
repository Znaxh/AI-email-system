"""Tier-2 system reliability from human feedback events.

All rates carry sample size + Wilson score confidence interval.
Slices with n < 20 render as insufficient_data.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

CRITICAL_LABELS = {
    "EDITED_MAJOR",
    "REJECTED",
    "ESCALATED_MISSED",
    "FLAGGED_HALLUCINATION",
}
CORRECT_LABELS = {"ACCEPTED_AS_IS", "EDITED_MINOR"}
NEUTRAL_LABELS = {"ESCALATED_CORRECTLY"}
MIN_N = 20


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (0.0, 0.0)
    # z for 95% ≈ 1.96; support common levels without scipy.
    z = {0.90: 1.64485, 0.95: 1.95996, 0.99: 2.57583}.get(confidence, 1.95996)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)
    low = max(0.0, (centre - margin) / denom)
    high = min(1.0, (centre + margin) / denom)
    return (round(low, 4), round(high, 4))


def _rate_payload(successes: int, n: int, *, invert: bool = False) -> dict:
    """Build a rate dict. If invert, successes are failures (report error rate)."""
    if n < MIN_N:
        return {
            "rate": None,
            "successes": successes,
            "n": n,
            "ci_low": None,
            "ci_high": None,
            "insufficient_data": True,
        }
    rate = successes / n
    if invert:
        # successes here means error count; interval on error proportion.
        lo, hi = wilson_interval(successes, n)
    else:
        lo, hi = wilson_interval(successes, n)
    return {
        "rate": round(rate, 4),
        "successes": successes,
        "n": n,
        "ci_low": lo,
        "ci_high": hi,
        "insufficient_data": False,
    }


def _group(events: Iterable[dict], groupby: str | None) -> dict[str, list[dict]]:
    if not groupby:
        return {"_all": list(events)}
    out: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        out[str(e.get(groupby) or "(none)")].append(e)
    return dict(out)


def compute_critical_error_rate(feedback_events: list[dict], groupby: str | None = None) -> dict:
    """Critical errors among human-audited/labeled AUTO responses only."""
    audited_auto = [
        e for e in feedback_events
        if e.get("routing_decision") == "auto" and e.get("label")
    ]
    result = {}
    for key, rows in _group(audited_auto, groupby).items():
        errors = sum(1 for e in rows if e.get("label") in CRITICAL_LABELS)
        payload = _rate_payload(errors, len(rows), invert=True)
        payload["audit_coverage"] = {
            "labeled_auto": len(rows),
            "note": "Denominator is human-labeled AUTO only; unaudited AUTO excluded.",
        }
        result[key] = payload
    return result if groupby else result.get("_all", _rate_payload(0, 0))


def compute_reliability(feedback_events: list[dict], groupby: str | None = None) -> dict:
    """Accepted / minor-edit rate among non-neutral labels."""
    usable = [e for e in feedback_events if e.get("label") not in NEUTRAL_LABELS and e.get("label")]
    result = {}
    for key, rows in _group(usable, groupby).items():
        ok = sum(1 for e in rows if e.get("label") in CORRECT_LABELS)
        result[key] = _rate_payload(ok, len(rows))
    return result if groupby else result.get("_all", _rate_payload(0, 0))


def compute_escalation_miss_rate(feedback_events: list[dict]) -> dict:
    """ESCALATED_MISSED / audited AUTO+REVIEW responses."""
    audited = [
        e for e in feedback_events
        if e.get("routing_decision") in ("auto", "review")
        and e.get("label") in (CRITICAL_LABELS | CORRECT_LABELS | {"ESCALATED_MISSED", "ESCALATED_CORRECTLY"})
    ]
    misses = sum(1 for e in audited if e.get("label") == "ESCALATED_MISSED")
    return _rate_payload(misses, len(audited), invert=True)


def compute_audit_coverage(feedback_events: list[dict], all_auto_count: int | None = None) -> dict:
    labeled_auto = sum(
        1 for e in feedback_events
        if e.get("routing_decision") == "auto" and e.get("label")
    )
    return {
        "labeled_auto": labeled_auto,
        "all_auto": all_auto_count,
        "coverage_rate": round(labeled_auto / all_auto_count, 4) if all_auto_count else None,
    }


def compute_calibration(feedback_events: list[dict]) -> dict:
    """quality_score decile vs real acceptance (ACCEPTED_AS_IS / EDITED_MINOR)."""
    rows = []
    for e in feedback_events:
        if e.get("label") in NEUTRAL_LABELS or not e.get("label"):
            continue
        ragas = e.get("ragas_scores") or e.get("ragas") or {}
        q = ragas.get("quality_score")
        if q is None:
            continue
        rows.append((float(q), e.get("label") in CORRECT_LABELS, e.get("label") in CRITICAL_LABELS))

    buckets = []
    for d in range(10):
        lo, hi = d / 10.0, (d + 1) / 10.0
        in_b = [r for r in rows if (lo <= r[0] < hi) or (d == 9 and r[0] == 1.0)]
        n = len(in_b)
        accepted = sum(1 for r in in_b if r[1])
        critical = sum(1 for r in in_b if r[2])
        buckets.append({
            "decile": d,
            "quality_lo": lo,
            "quality_hi": hi,
            "n": n,
            "acceptance_rate": round(accepted / n, 4) if n else None,
            "critical_error_rate": round(critical / n, 4) if n else None,
            "insufficient_data": n < MIN_N,
        })
    return {"buckets": buckets, "n_total": len(rows)}


def trend_by_week(feedback_events: list[dict], metric: str = "reliability") -> list[dict]:
    by_week: dict[str, list[dict]] = defaultdict(list)
    for e in feedback_events:
        ts = e.get("timestamp") or ""
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.now(timezone.utc)
        week = dt.strftime("%G-W%V")
        by_week[week].append(e)
    out = []
    for week in sorted(by_week):
        if metric == "critical_error":
            payload = compute_critical_error_rate(by_week[week])
        else:
            payload = compute_reliability(by_week[week])
        out.append({"week": week, **payload})
    return out


def _demo() -> None:
    lo, hi = wilson_interval(8, 10)
    assert 0.4 < lo < 0.8 < hi < 1.0
    assert wilson_interval(0, 0) == (0.0, 0.0)

    events = [
        {"routing_decision": "auto", "label": "ACCEPTED_AS_IS", "ragas_scores": {"quality_score": 0.9}},
        {"routing_decision": "auto", "label": "EDITED_MAJOR", "ragas_scores": {"quality_score": 0.85}},
        {"routing_decision": "auto", "label": "FLAGGED_HALLUCINATION", "ragas_scores": {"quality_score": 0.92}},
    ] + [
        {"routing_decision": "auto", "label": "ACCEPTED_AS_IS", "ragas_scores": {"quality_score": 0.7}}
        for _ in range(20)
    ]
    crit = compute_critical_error_rate(events)
    assert crit["n"] >= 20
    assert crit["rate"] is not None
    rel = compute_reliability(events)
    assert rel["rate"] is not None
    cal = compute_calibration(events)
    assert len(cal["buckets"]) == 10
    print("reliability self-check OK")


if __name__ == "__main__":
    _demo()
