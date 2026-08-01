"""Retriever empty/thin corpus and split normalization."""

from src.retriever import TicketRetriever
from src.schema import Ticket


def _t(tid: str, split: str = "corpus", email: str = "hello returns"):
    return Ticket(
        ticket_id=tid,
        order_id="",
        category="x",
        split=split,
        incoming_email=email,
        actual_reply="hi",
    )


def test_empty_retriever_safe():
    r = TicketRetriever([])
    assert r.top_k("anything") == []
    assert r.disable_neighbors is True


def test_thin_corpus_disables_neighbors():
    tickets = [_t(f"T{i}", email=f"email {i} about returns") for i in range(5)]
    r = TicketRetriever(tickets)
    assert r.disable_neighbors is True
    assert r.top_k("returns") == []

    many = tickets + [_t(f"U{i}", email=f"more email {i} shipping delay") for i in range(12)]
    r2 = TicketRetriever(many)
    assert r2.disable_neighbors is False
    assert len(r2.top_k("returns", k=2)) == 2


def test_holdout_never_retrievable_and_unrecognized_split_is_corpus():
    tickets = [
        _t("H1", split="holdout", email="holdout only secret"),
        _t("C1", split="train", email="corpus train returns"),
        _t("C2", split="weird", email="weird split shipping"),
        _t("C3", split="corpus", email="normal corpus refund"),
    ]
    r = TicketRetriever(tickets, disable_neighbors=False)
    ids = {t.ticket_id for t in r.tickets}
    assert "H1" not in ids
    assert {"C1", "C2", "C3"} <= ids
