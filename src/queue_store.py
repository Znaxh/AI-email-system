"""Review/action queue. Public API unchanged; persistence via StructuredStore.

# ponytail: structured-store adapter keeps the same upsert/list/set_status surface.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.storage.factory import get_structured_store

TABLE = "queue"

# decision -> base weight for priority ordering (higher surfaces first)
DECISION_WEIGHT = {"escalate": 300.0, "review": 200.0, "auto": 100.0, "ignore": 0.0}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store():
    return get_structured_store()


def upsert(item: dict, db_path=None) -> None:
    """Insert a routed item, or refresh it if the same email_id is re-routed.
    A human status (sent/simulated/dismissed) is preserved on re-route.

    db_path is accepted for backward-compat with self-checks but ignored —
    persistence goes through the configured StructuredStore.
    """
    store = _store()
    email_id = item["email_id"]
    existing = store.get(TABLE, email_id)
    status = item.get("status", "pending")
    if existing and existing.get("status") not in (None, "pending"):
        status = existing["status"]

    now = _now()
    record = {
        "id": email_id,
        "email_id": email_id,
        "thread_id": item.get("thread_id", ""),
        "from_addr": item.get("from_addr", ""),
        "subject": item.get("subject", ""),
        "body": item.get("body", ""),
        "order_id": item.get("order_id", ""),
        "category": item.get("category", ""),
        "decision": item["decision"],
        "status": status,
        "confidence": item.get("confidence", 0.0),
        "priority": item.get("priority", 0.0),
        "suggested_reply": item.get("suggested_reply", ""),
        "judge": item.get("judge", {}),
        "flags": item.get("flags", []),
        "response_id": item.get("response_id", ""),
        "original_reply": item.get("original_reply", item.get("suggested_reply", "")),
        "remedy": item.get("remedy", {}),
        "ragas": item.get("ragas", {}),
        "retrieved_rule_ids": item.get("retrieved_rule_ids", []),
        "cited_rule_ids": item.get("cited_rule_ids", []),
        "audit_sample": item.get("audit_sample", False),
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
        "sent_at": (existing or {}).get("sent_at"),
    }
    store.insert(TABLE, record)


def list_items(
    decision: str | None = None,
    status: str | None = None,
    db_path=None,
) -> list[dict]:
    """Items, highest priority first. Optional decision/status filters."""
    filters = {}
    if decision:
        filters["decision"] = decision
    if status:
        filters["status"] = status
    items = _store().query(TABLE, filters=filters or None, order_by="-priority")
    # Stable secondary sort by created_at ascending within same priority.
    items.sort(key=lambda i: (-float(i.get("priority") or 0), i.get("created_at") or ""))
    return items


def get(email_id: str, db_path=None) -> dict | None:
    return _store().get(TABLE, email_id)


def set_status(
    email_id: str,
    status: str,
    *,
    suggested_reply: str | None = None,
    db_path=None,
) -> None:
    now = _now()
    patch: dict = {"status": status, "updated_at": now}
    if status in ("sent", "simulated"):
        patch["sent_at"] = now
    if suggested_reply is not None:
        patch["suggested_reply"] = suggested_reply
    _store().update(TABLE, email_id, patch)


def counts(db_path=None) -> dict:
    """Pending counts per decision, plus total pending — for dashboard badges."""
    store = _store()
    by = {}
    for decision in ("escalate", "review", "auto"):
        by[decision] = store.count(TABLE, {"decision": decision, "status": "pending"})
    return {
        "escalate": by["escalate"],
        "review": by["review"],
        "auto": by["auto"],
        "pending_total": by["escalate"] + by["review"] + by["auto"],
    }


def _demo() -> None:
    """Offline self-check against an isolated local store root."""
    import tempfile
    from pathlib import Path

    from src.storage.factory import clear_store_cache
    from src.storage.local import LocalSQLiteStore
    import src.queue_store as qs

    with tempfile.TemporaryDirectory() as tmp:
        clear_store_cache()
        # Patch module store to use temp root.
        tmp_store = LocalSQLiteStore(root=tmp)
        original = qs._store
        qs._store = lambda: tmp_store
        try:
            upsert({"email_id": "e1", "decision": "review", "priority": 200.0})
            upsert({"email_id": "e2", "decision": "escalate", "priority": 300.0})
            upsert({"email_id": "e3", "decision": "auto", "priority": 100.0})

            items = list_items()
            assert [i["email_id"] for i in items] == ["e2", "e1", "e3"], "priority ordering"
            assert counts()["pending_total"] == 3
            assert len(list_items(decision="escalate")) == 1

            set_status("e2", "sent")
            assert get("e2")["status"] == "sent"
            assert counts()["escalate"] == 0, "sent item drops out of pending"

            upsert({"email_id": "e2", "decision": "escalate", "priority": 300.0})
            assert get("e2")["status"] == "sent", "human status preserved on re-route"
        finally:
            qs._store = original
            clear_store_cache()
    print("queue_store self-check OK")


if __name__ == "__main__":
    _demo()
