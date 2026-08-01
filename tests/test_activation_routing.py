"""Activation tests for versioned company-data bundles (no api/pipeline imports)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.company_data.schema import FieldMapping
from src.company_data.service import (
    activate_staged,
    load_active_company_bundle,
    rollback,
    stage_upload,
    status,
)


def _stage_text(tmp_path: Path, name: str, text: str, target: str):
    path = tmp_path / name
    path.write_text(text)
    return stage_upload(path.read_bytes(), filename=name, target=target)


def _minimal_policy_md(tmp_path: Path) -> dict:
    text = """# R1 Returns - standard window
A return within 30 days MUST be granted a full refund. Worn items do NOT qualify.

# R2 High value
Any case exceeding $200 MUST be escalated to a human senior agent.

# R3 Polite closing
Agents MUST thank the customer. This rule has no order conditions.
"""
    path = tmp_path / "policy.md"
    path.write_text(text)
    return stage_upload(path.read_bytes(), filename="policy.md", target="policy")


@pytest.fixture()
def isolated_blob(tmp_path, monkeypatch):
    """Point LocalFileBlobStore root at a temp directory."""
    root = tmp_path / "blobroot"
    root.mkdir()
    monkeypatch.setenv("STORAGE_BLOB_PROVIDER", "local")
    monkeypatch.setenv("STORAGE_STRUCTURED_PROVIDER", "local")
    from src.storage import factory
    from src.storage.local import LocalFileBlobStore, LocalSQLiteStore

    factory.clear_store_cache()
    monkeypatch.setattr(factory, "get_blob_store", lambda: LocalFileBlobStore(root=root))
    monkeypatch.setattr(factory, "get_structured_store", lambda: LocalSQLiteStore(root=root))
    import src.company_data.service as svc

    monkeypatch.setattr(svc, "_POLICY_CACHE_DIR", tmp_path / "policy_cache" / "policy")
    (tmp_path / "policy_cache" / "policy").mkdir(parents=True)
    yield root
    factory.clear_store_cache()


def _txn_mapping() -> dict:
    return FieldMapping(
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


def test_activate_builds_versioned_bundle(isolated_blob, tmp_path):
    pol = _minimal_policy_md(tmp_path)
    txn = _stage_text(
        tmp_path,
        "t1.csv",
        "order_id,customer_id,price,order_date,status\nO1,C1,10,2026-01-15,delivered\n",
        "transactions",
    )
    cfg = {"use_embeddings": False, "policy_llm_chunking": False}
    b1 = activate_staged(
        {
            "policy": {"token": pol["token"], "mapping": {}, "file_hash": pol["file_hash"]},
            "transactions": {
                "token": txn["token"],
                "mapping": _txn_mapping(),
                "file_hash": txn["file_hash"],
            },
        },
        confirm_degraded=True,
        config=cfg,
    )
    assert status()["active_version"] == b1.version_id
    assert "O1" in b1.transactions
    assert len(b1.policy_store.rules) >= 2
    assert b1.quality.get("txn_verdict") == "READY"

    loaded = load_active_company_bundle(config=cfg)
    assert loaded.version_id == b1.version_id
    assert "O1" in loaded.transactions


def test_degraded_records_missing_fields_not_file_flags(isolated_blob, tmp_path):
    pol = _minimal_policy_md(tmp_path)
    txn = _stage_text(
        tmp_path,
        "noprice.csv",
        "order_id,customer_id,order_date,status\nO1,C1,2026-01-15,delivered\n",
        "transactions",
    )
    mapping = FieldMapping(
        fields={
            "order_id": "order_id",
            "customer_id": "customer_id",
            "order_date": "order_date",
            "status": "status",
            "price": None,
        },
        date_orders={"order_date": "YMD"},
    ).to_dict()
    cfg = {"use_embeddings": False, "policy_llm_chunking": False}
    bundle = activate_staged(
        {
            "policy": {"token": pol["token"], "mapping": {}, "file_hash": pol["file_hash"]},
            "transactions": {"token": txn["token"], "mapping": mapping, "file_hash": txn["file_hash"]},
        },
        confirm_degraded=True,
        config=cfg,
    )
    assert bundle.quality.get("txn_verdict") == "DEGRADED"
    assert "price" in bundle.transaction_missing_fields.get("O1", frozenset())
    # Metadata only — quality dict must not be treated as email flags.
    assert "unverifiable:order_value" not in (bundle.quality.get("advisories") or [])


def test_failed_activation_leaves_active_unchanged(isolated_blob, tmp_path):
    pol = _minimal_policy_md(tmp_path)
    txn = _stage_text(
        tmp_path,
        "ok.csv",
        "order_id,customer_id,price,order_date,status\nO1,C1,10,2026-01-15,delivered\n",
        "transactions",
    )
    cfg = {"use_embeddings": False, "policy_llm_chunking": False}
    b1 = activate_staged(
        {
            "policy": {"token": pol["token"], "mapping": {}, "file_hash": pol["file_hash"]},
            "transactions": {
                "token": txn["token"],
                "mapping": _txn_mapping(),
                "file_hash": txn["file_hash"],
            },
        },
        confirm_degraded=True,
        config=cfg,
    )
    active = b1.version_id

    bad = _stage_text(tmp_path, "bad.csv", "foo,bar\n1,2\n", "transactions")
    with pytest.raises(ValueError):
        activate_staged(
            {
                "policy": {"token": pol["token"], "mapping": {}, "file_hash": pol["file_hash"]},
                "transactions": {
                    "token": bad["token"],
                    "mapping": {"fields": {}},
                    "file_hash": bad["file_hash"],
                },
            },
            confirm_degraded=True,
            config=cfg,
        )
    assert status()["active_version"] == active


from src.company_data.quality import compute_email_quality_flags
from src.router import _decide
from src.schema import GeneratedReply, PolicyRule, Remedy


def test_quality_flags_per_email_not_file_wide():
    missing = frozenset({"price"})
    rule_no_price = PolicyRule(id="R1", text="Always be polite.", depends_on=[], dependency_status="resolved")
    rule_price = PolicyRule(
        id="R2",
        text="Orders over $200 MUST escalate.",
        condition="order total exceeds threshold",
        depends_on=["price"],
        dependency_status="resolved",
    )
    rules = {"R1": rule_no_price, "R2": rule_price}
    gen_ok = GeneratedReply(
        ticket_id="t", reply="ok", remedy=Remedy(rule_cited="R1"),
        cited_rule_ids=["R1"], retrieved_rule_ids=["R1"],
    )
    assert compute_email_quality_flags(
        used_placeholder=False, order_id="O1", missing_fields=missing, gen=gen_ok, rules_by_id=rules,
    ) == []
    gen_price = GeneratedReply(
        ticket_id="t", reply="ok", remedy=Remedy(rule_cited="R2"),
        cited_rule_ids=["R2"], retrieved_rule_ids=["R2"],
    )
    assert compute_email_quality_flags(
        used_placeholder=False, order_id="O1", missing_fields=missing, gen=gen_price, rules_by_id=rules,
    ) == ["unverifiable:order_value"]
    assert compute_email_quality_flags(
        used_placeholder=False, order_id="O2", missing_fields=frozenset(), gen=gen_price, rules_by_id=rules,
    ) == []


def test_placeholder_only_no_transaction_flag():
    gen = GeneratedReply(ticket_id="t", reply="ok", cited_rule_ids=["R1"], retrieved_rule_ids=["R1"])
    flags = compute_email_quality_flags(
        used_placeholder=True, order_id="", missing_fields=frozenset({"price", "status"}),
        gen=gen, rules_by_id={},
    )
    assert flags == ["unverifiable:no_transaction"]


def test_degraded_does_not_block_auto_path():
    assert _decide(95, [], False, False, 80, 50) == "auto"
    assert _decide(95, ["unverifiable:order_value"], False, False, 80, 50) == "review"


def test_dataset_warnings_never_in_ev_flags():
    # File-level advisories must never be copied into per-email flags.
    from src.company_data.schema import FIELD_FLAG_MAP
    assert "weak:tone_corpus" not in FIELD_FLAG_MAP.values()


def test_activate_and_rollback_restores_index(isolated_blob, tmp_path):
    pol = _minimal_policy_md(tmp_path)
    txn1 = _stage_text(
        tmp_path,
        "t1.csv",
        "order_id,customer_id,price,order_date,status\nO1,C1,10,2026-01-15,delivered\n",
        "transactions",
    )
    cfg = {"use_embeddings": False, "policy_llm_chunking": False}
    b1 = activate_staged(
        {
            "policy": {"token": pol["token"], "mapping": {}, "file_hash": pol["file_hash"]},
            "transactions": {"token": txn1["token"], "mapping": _txn_mapping(), "file_hash": txn1["file_hash"]},
        },
        confirm_degraded=True,
        config=cfg,
    )
    v1 = b1.version_id
    txn2 = _stage_text(
        tmp_path,
        "t2.csv",
        "order_id,customer_id,price,order_date,status\nO2,C2,20,2026-02-15,delivered\n",
        "transactions",
    )
    pol2 = _minimal_policy_md(tmp_path)
    activate_staged(
        {
            "policy": {"token": pol2["token"], "mapping": {}, "file_hash": pol2["file_hash"]},
            "transactions": {"token": txn2["token"], "mapping": _txn_mapping(), "file_hash": txn2["file_hash"]},
        },
        confirm_degraded=True,
        config=cfg,
    )
    rolled = rollback(v1, config=cfg)
    assert rolled.version_id == v1
    assert "O1" in rolled.transactions
    assert "O2" not in rolled.transactions
    assert len(rolled.policy_store.rules) >= 2
    assert rolled.policy_store.retrieve("refund", k=1)
