"""Durable queue between ingestion and routing.

publish() is idempotent per email_id. drain() isolates each email in its own
try/except — retry up to MAX_ATTEMPTS, then dead-letter.

Persistence goes through StructuredStore (logical table "event_bus").
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from src.schema import IncomingEmail
from src.storage.factory import get_structured_store

TABLE = "event_bus"
MAX_ATTEMPTS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store():
    return get_structured_store()


def publish(email: IncomingEmail, db_path=None) -> None:
    """Enqueue a parsed email. Re-publishing resets to pending with fresh payload."""
    now = _now()
    _store().insert(
        TABLE,
        {
            "id": email.id,
            "email_id": email.id,
            "payload": email.model_dump(),
            "status": "pending",
            "attempts": 0,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        },
    )


def _pending(limit: int) -> list[dict]:
    rows = _store().query(TABLE, filters={"status": "pending"}, order_by="created_at")
    return [r for r in rows if int(r.get("attempts") or 0) < MAX_ATTEMPTS][:limit]


def _ack(email_id: str) -> None:
    _store().update(TABLE, email_id, {"status": "done", "updated_at": _now()})


def _fail(email_id: str, attempts: int, error: str) -> str:
    attempts += 1
    status = "dead_letter" if attempts >= MAX_ATTEMPTS else "pending"
    _store().update(
        TABLE,
        email_id,
        {
            "status": status,
            "attempts": attempts,
            "last_error": error[:2000],
            "updated_at": _now(),
        },
    )
    return status


def drain(
    process: Callable[[IncomingEmail], dict],
    limit: int = 100,
    db_path=None,
) -> list[dict]:
    """Process every pending event. One failure never blocks the rest."""
    results = []
    for row in _pending(limit):
        payload = row.get("payload") or {}
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        email = IncomingEmail(**payload)
        try:
            outcome = process(email)
            _ack(email.id)
            results.append(
                {"email_id": email.id, "ok": True, "result": outcome, "error": None, "status": "done"}
            )
        except Exception as exc:
            status = _fail(email.id, int(row.get("attempts") or 0), f"{type(exc).__name__}: {exc}")
            results.append(
                {"email_id": email.id, "ok": False, "result": None, "error": str(exc), "status": status}
            )
    return results


def dead_letters(db_path=None) -> list[dict]:
    rows = _store().query(TABLE, filters={"status": "dead_letter"}, order_by="-updated_at")
    return rows


def _demo() -> None:
    import tempfile

    from src.storage.factory import clear_store_cache
    from src.storage.local import LocalSQLiteStore
    import src.event_bus as eb

    with tempfile.TemporaryDirectory() as tmp:
        clear_store_cache()
        tmp_store = LocalSQLiteStore(root=tmp)
        original = eb._store
        eb._store = lambda: tmp_store
        try:
            publish(IncomingEmail(id="ok-1", body="fine"))
            publish(IncomingEmail(id="bad-1", body="boom"))
            publish(IncomingEmail(id="ok-2", body="fine too"))

            def flaky(email: IncomingEmail) -> dict:
                if email.id == "bad-1":
                    raise RuntimeError("simulated processing failure")
                return {"processed": email.id}

            results = drain(flaky)
            by_id = {r["email_id"]: r for r in results}
            assert by_id["ok-1"]["ok"] and by_id["ok-2"]["ok"]
            assert not by_id["bad-1"]["ok"] and by_id["bad-1"]["status"] == "pending"

            drain(flaky)
            final = drain(flaky)
            assert final[0]["status"] == "dead_letter", final
            assert len(dead_letters()) == 1

            publish(IncomingEmail(id="bad-1", body="fixed now"))
            recovered = drain(lambda e: {"processed": e.id})
            assert recovered[0]["ok"] is True
            assert len(dead_letters()) == 0
        finally:
            eb._store = original
            clear_store_cache()
    print("event_bus self-check OK")


if __name__ == "__main__":
    _demo()
