"""API tests: stage → preview → dry-run → activate against a temp BlobStore."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
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
    api._clear_resources()
    client = TestClient(api.app)
    yield client
    api._clear_resources()
    factory.clear_store_cache()


def test_stage_preview_dry_run_activate(api_client, tmp_path):
    policy = (
        b"# R1 Returns\nA return within 30 days MUST be refunded.\n\n"
        b"# R2 Thanks\nAgents MUST thank the customer.\n"
    )
    txns = b"order_id,customer_id,price,order_date,status\nO1,C1,10,2026-01-15,delivered\n"

    st = api_client.get("/api/settings/company-data")
    assert st.status_code == 200
    assert st.json().get("setup_required") is True

    pol = api_client.post(
        "/api/settings/company-data/stage?target=policy",
        files={"file": ("policy.md", policy, "text/markdown")},
    )
    assert pol.status_code == 200, pol.text
    pol_token = pol.json()["token"]

    txn = api_client.post(
        "/api/settings/company-data/stage?target=transactions",
        files={"file": ("txns.csv", txns, "text/csv")},
    )
    assert txn.status_code == 200, txn.text
    txn_token = txn.json()["token"]
    txn_hash = txn.json()["file_hash"]

    prev = api_client.get(f"/api/settings/company-data/preview/{txn_token}")
    assert prev.status_code == 200
    body = prev.json()
    assert "order_id" in body["columns"]
    mapping = body["suggested_mapping"]
    mapping["date_orders"] = {"order_date": "YMD"}
    mapping["money_styles"] = {"price": "dot_decimal"}

    dry = api_client.post(
        f"/api/settings/company-data/dry-run/{txn_token}",
        json={"mapping": mapping, "file_hash": txn_hash},
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["verdict"] == "READY"

    act = api_client.post(
        "/api/settings/company-data/activate",
        json={
            "policy": {"token": pol_token, "mapping": {}, "file_hash": pol.json()["file_hash"]},
            "transactions": {"token": txn_token, "mapping": mapping, "file_hash": txn_hash},
            "confirm_degraded": False,
        },
    )
    assert act.status_code == 200, act.text
    assert act.json()["ok"] is True
    assert act.json()["version_id"]

    st2 = api_client.get("/api/settings/company-data")
    assert st2.json().get("setup_required") is False
    assert st2.json().get("active_version") == act.json()["version_id"]
