"""In-flight company_data_version pinning and Assistant mismatch 409."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def ready_api(tmp_path, monkeypatch):
    root = tmp_path / "blobroot"
    root.mkdir()
    monkeypatch.setenv("STORAGE_BLOB_PROVIDER", "local")
    monkeypatch.setenv("STORAGE_STRUCTURED_PROVIDER", "local")
    from src.storage import factory
    from src.storage.local import LocalFileBlobStore, LocalSQLiteStore

    factory.clear_store_cache()
    monkeypatch.setattr(factory, "get_blob_store", lambda: LocalFileBlobStore(root=root))
    monkeypatch.setattr(factory, "get_structured_store", lambda: LocalSQLiteStore(root=root))

    import api
    import src.company_data.service as svc

    monkeypatch.setattr(svc, "_POLICY_CACHE_DIR", tmp_path / "policy_cache" / "policy")
    monkeypatch.setattr(svc, "maybe_import_legacy_data", lambda: False)
    (tmp_path / "policy_cache" / "policy").mkdir(parents=True)

    from src.company_data.schema import FieldMapping
    from src.company_data.service import activate_staged, stage_upload

    pol = stage_upload(
        (
            b"# R1 Returns - standard window\n"
            b"A return within 30 days MUST be granted a full refund.\n\n"
            b"# R3 Polite closing\n"
            b"Agents MUST thank the customer. This rule has no order conditions.\n"
        ),
        filename="policy.md",
        target="policy",
    )
    txn = stage_upload(
        b"order_id,customer_id,price,order_date,status\nO1,C1,10,2026-01-15,delivered\n",
        filename="t.csv",
        target="transactions",
    )
    mapping = FieldMapping(
        fields={
            "order_id": "order_id",
            "customer_id": "customer_id",
            "price": "price",
            "order_date": "order_date",
            "status": "status",
        },
        date_orders={"order_date": "YMD"},
        money_styles={"price": "dot_decimal"},
    ).to_dict()
    activate_staged(
        {
            "policy": {"token": pol["token"], "mapping": {}, "file_hash": pol["file_hash"]},
            "transactions": {"token": txn["token"], "mapping": mapping, "file_hash": txn["file_hash"]},
        },
        confirm_degraded=True,
        config={"use_embeddings": False, "policy_llm_chunking": False},
    )
    api._clear_resources()
    yield TestClient(api.app)
    api._clear_resources()
    factory.clear_store_cache()


def test_assistant_mismatch_returns_409(ready_api):
    boot = ready_api.get("/api/bootstrap").json()
    assert boot["company_data_version"]
    bad = ready_api.post(
        "/api/assistant/suggest",
        json={
            "email": "hello",
            "company_data_version": "stale-version",
        },
    )
    assert bad.status_code == 409


def test_queue_item_stamps_company_data_version(ready_api, monkeypatch):
    import api

    boot = ready_api.get("/api/bootstrap").json()
    version = boot["company_data_version"]

    def fake_route_email(*a, **k):
        return {
            "email_id": "e1",
            "decision": "review",
            "status": "pending",
            "confidence": 50,
            "priority": 1,
            "suggested_reply": "hi",
            "original_reply": "hi",
            "judge": {},
            "ragas": {},
            "remedy": {},
            "flags": [],
            "response_id": "r1",
            "audit_sample": False,
            "company_data_version": k.get("company_data_version") or version,
            "category": "x",
            "order_id": "O1",
            "subject": "",
            "body": "hi",
            "from_addr": "a@b.c",
            "thread_id": "",
        }

    monkeypatch.setattr(api.router, "route_email", fake_route_email)
    out = ready_api.post("/api/inbox/route-one", json={"body": "I need a refund for O1"})
    assert out.status_code == 200
    assert out.json()["company_data_version"] == version
