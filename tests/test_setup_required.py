"""Setup-required empty-state: bootstrap/settings must not crash without an active bundle."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def empty_api(tmp_path, monkeypatch):
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
    yield TestClient(api.app)
    api._clear_resources()
    factory.clear_store_cache()


def test_bootstrap_and_settings_when_setup_required(empty_api):
    boot = empty_api.get("/api/bootstrap")
    assert boot.status_code == 200
    assert boot.json()["setup_required"] is True
    assert boot.json()["orders"] == []

    settings = empty_api.get("/api/settings")
    assert settings.status_code == 200
    body = settings.json()
    assert body["company_data"]["setup_required"] is True
    assert body["policy"]["filename"] == "(none)"

    suggest = empty_api.post("/api/assistant/suggest", json={"email": "hello"})
    assert suggest.status_code == 409
