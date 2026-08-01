"""Human feedback capture: remedy extraction/diff + feedback_events persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src import llm_client, prompts
from src.schema import Remedy
from src.storage.factory import get_structured_store

TABLE = "feedback_events"

LABELS = (
    "ACCEPTED_AS_IS",
    "EDITED_MINOR",
    "EDITED_MAJOR",
    "REJECTED",
    "ESCALATED_CORRECTLY",
    "ESCALATED_MISSED",
    "FLAGGED_HALLUCINATION",
)


def extract_remedy(reply: str) -> Remedy:
    """Schema-only extraction — not a judgment call."""
    raw = llm_client.complete(
        prompts.REMEDY_EXTRACT_SYSTEM,
        prompts.REMEDY_EXTRACT_USER.format(reply=reply),
        max_tokens=200,
        purpose="classify",
    )
    try:
        data = llm_client.extract_json(raw)
    except Exception:
        data = {}
    amount = data.get("remedy_amount")
    try:
        amount = float(amount) if amount is not None and amount != "" else None
    except (TypeError, ValueError):
        amount = None
    return Remedy(
        remedy_type=str(data.get("remedy_type") or ""),
        remedy_amount=amount,
        rule_cited=str(data.get("rule_cited") or "").upper(),
        escalate=bool(data.get("escalate")),
    )


def remedy_diff(original: Remedy | dict, edited: Remedy | dict) -> dict:
    """Compare structured remedy fields. Any change → major."""
    def _as_dict(r: Remedy | dict) -> dict:
        if isinstance(r, Remedy):
            return r.model_dump()
        return dict(r or {})

    a, b = _as_dict(original), _as_dict(edited)
    changed = {}
    for key in ("remedy_type", "remedy_amount", "rule_cited", "escalate"):
        av, bv = a.get(key), b.get(key)
        # Normalize amounts
        if key == "remedy_amount":
            try:
                av = float(av) if av is not None and av != "" else None
            except (TypeError, ValueError):
                pass
            try:
                bv = float(bv) if bv is not None and bv != "" else None
            except (TypeError, ValueError):
                pass
        if key == "rule_cited":
            av = str(av or "").upper()
            bv = str(bv or "").upper()
        if av != bv:
            changed[key] = {"from": av, "to": bv}
    return changed


def classify_send_label(original_reply: str, edited_reply: str, original_remedy: Remedy | dict) -> tuple[str, dict]:
    """Deterministic label for Send actions."""
    if edited_reply.strip() == (original_reply or "").strip():
        return "ACCEPTED_AS_IS", {}
    edited_remedy = extract_remedy(edited_reply)
    diff = remedy_diff(original_remedy, edited_remedy)
    if diff:
        return "EDITED_MAJOR", {"diff": diff, "edited_remedy": edited_remedy.model_dump()}
    return "EDITED_MINOR", {"diff": {}, "edited_remedy": edited_remedy.model_dump()}


def record_feedback(
    *,
    response_id: str,
    ticket_id: str,
    category: str,
    cited_rule: str,
    routing_decision: str,
    ragas_scores: dict | None,
    label: str,
    remedy_diff_payload: dict | None = None,
    reviewer: str = "agent",
    extra: dict | None = None,
) -> str:
    if label not in LABELS:
        raise ValueError(f"unknown feedback label: {label!r}")
    store = get_structured_store()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record: dict[str, Any] = {
        "id": f"{response_id}:{label}:{now}",
        "response_id": response_id,
        "ticket_id": ticket_id,
        "category": category,
        "cited_rule": cited_rule,
        "routing_decision": routing_decision,
        "ragas_scores": ragas_scores or {},
        "label": label,
        "remedy_diff": remedy_diff_payload,
        "reviewer": reviewer,
        "timestamp": now,
    }
    if extra:
        record.update(extra)
    return store.insert(TABLE, record)


def list_feedback(filters: dict | None = None) -> list[dict]:
    return get_structured_store().query(TABLE, filters=filters, order_by="-timestamp")


def _demo() -> None:
    a = Remedy(remedy_type="refund", remedy_amount=88.0, rule_cited="R1.1", escalate=False)
    b = Remedy(remedy_type="refund", remedy_amount=88.0, rule_cited="R1.1", escalate=False)
    assert remedy_diff(a, b) == {}
    c = Remedy(remedy_type="refund", remedy_amount=50.0, rule_cited="R1.1", escalate=False)
    assert "remedy_amount" in remedy_diff(a, c)
    d = Remedy(remedy_type="refund", remedy_amount=88.0, rule_cited="R2.1", escalate=False)
    assert "rule_cited" in remedy_diff(a, d)
    print("feedback self-check OK")


if __name__ == "__main__":
    _demo()
