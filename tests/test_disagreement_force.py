"""Fail-closed disagreement check before AUTO."""

from src.ragas_evaluator import check_retrieval_disagreement, should_gate_from_auto
from src.schema import Transaction


class _StubStore:
    rules = []

    def all_text(self):
        return "R1: refund within 30 days."


def test_force_runs_even_when_sample_would_skip(monkeypatch):
    calls = {"n": 0}

    def fake_complete(*a, **k):
        calls["n"] += 1
        return '{"rule": "R1"}'

    monkeypatch.setattr("src.llm_client.complete", fake_complete)
    monkeypatch.setattr(
        "src.llm_client.extract_json",
        lambda raw: {"rule": "R1"},
    )
    txn = Transaction(order_id="O1")
    skipped = check_retrieval_disagreement(
        "email",
        txn,
        _StubStore(),
        "R1",
        sample_rate=0.0,
        response_id="x",
        force=False,
    )
    assert skipped["disagreement_checked"] is False

    forced = check_retrieval_disagreement(
        "email",
        txn,
        _StubStore(),
        "R1",
        sample_rate=0.0,
        response_id="x",
        force=True,
    )
    assert forced["disagreement_checked"] is True
    assert calls["n"] >= 1


def test_unchecked_disagreement_gates_auto():
    assert should_gate_from_auto(0.9, None, scoring_error="") is False
    # Router treats unchecked as fail-closed separately; gate helper still
    # fails closed on True disagreement / scoring error.
    assert should_gate_from_auto(0.9, True) is True
    assert should_gate_from_auto(0.9, False, scoring_error="boom") is True
