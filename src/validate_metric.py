"""Calibration / reliability reporting (replaces legacy expected_outcomes checks).

No hand labels. No synthetic controls. Reports are derived from feedback_events
and ragas_scores via the pluggable structured store.
"""

from __future__ import annotations

from src.feedback import list_feedback
from src.reliability import (
    compute_audit_coverage,
    compute_calibration,
    compute_critical_error_rate,
    compute_escalation_miss_rate,
    compute_reliability,
    trend_by_week,
)
from src.storage.factory import get_structured_store


def build_reliability_report() -> dict:
    events = list_feedback()
    auto_total = get_structured_store().count("queue", {"decision": "auto"})
    return {
        "critical_error_rate": compute_critical_error_rate(events),
        "critical_error_by_category": compute_critical_error_rate(events, groupby="category"),
        "critical_error_by_rule": compute_critical_error_rate(events, groupby="cited_rule"),
        "reliability_rate": compute_reliability(events),
        "reliability_by_category": compute_reliability(events, groupby="category"),
        "escalation_miss_rate": compute_escalation_miss_rate(events),
        "audit_coverage": compute_audit_coverage(events, all_auto_count=auto_total),
        "calibration": compute_calibration(events),
        "weekly_reliability": trend_by_week(events, "reliability"),
        "weekly_critical_error": trend_by_week(events, "critical_error"),
        "n_feedback_events": len(events),
    }


def validate() -> dict:
    """Entry point used by pipeline.py --validate."""
    return build_reliability_report()


def _demo() -> None:
    report = build_reliability_report()
    assert "critical_error_rate" in report
    assert "calibration" in report
    print("validate_metric self-check OK", {"n": report["n_feedback_events"]})


if __name__ == "__main__":
    _demo()
