"""Response evaluation: RAGAS Tier-1 quality + deterministic diagnostics.

Replaces the old Layer A/B/C compliance/alignment/quality judges.
Scores are never blended with human-feedback reliability (Tier-2).
"""

from __future__ import annotations

import difflib
import re

from src.config import load_config
from src.policy_store import PolicyStore
from src.ragas_evaluator import evaluate_ragas
from src.schema import EvaluationResult, GeneratedReply, Remedy, Ticket, Transaction

ABSOLUTE_PHRASES = [
    "we guarantee", "guaranteed", "100%", "no matter what", "always refund",
    "never fails", "under any circumstances", "in all cases",
]


def deterministic_checks(reply: str, order_id: str, policy_text: str) -> tuple[int, list[str]]:
    penalty = 0
    flags: list[str] = []
    lower = reply.lower()

    if re.search(r"\[[A-Z_ ]+\]|\{[A-Z_ ]+\}|TBD|TODO", reply):
        penalty += 8
        flags.append("placeholder_tokens (-8)")

    words = reply.split()
    if len(words) < 40 or len(words) > 250:
        penalty += 4
        flags.append("length_outside_40_250 (-4)")

    policy_l = policy_text.lower()
    for phrase in ABSOLUTE_PHRASES:
        if phrase in lower and phrase not in policy_l:
            penalty += 8
            flags.append(f"unqualified_absolute:{phrase} (-8)")
            break

    if order_id and order_id.lower() not in lower:
        penalty += 4
        flags.append("order_id_not_referenced (-4)")

    return min(penalty, 20), flags


def lexical_overlap(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def evaluate_reply(
    ticket: Ticket,
    transaction: Transaction,
    reply: str,
    policy_store: PolicyStore,
    reply_source: str,
    *,
    retrieved_rule_ids: list[str] | None = None,
    cited_rule_ids: list[str] | None = None,
    retrieved_contexts: list[str] | None = None,
    response_id: str = "",
    remedy: Remedy | None = None,
    persist: bool = True,
) -> EvaluationResult:
    """Score one reply with RAGAS + deterministic checks. No blended final score."""
    cfg = load_config()
    contexts = retrieved_contexts
    if contexts is None:
        # Fall back to retrieving again only when caller did not pass generation contexts.
        contexts = policy_store.retrieve(
            f"{ticket.incoming_email}\n{transaction.status} {transaction.product}",
            k=int(cfg.get("k_policy", 4)),
        )

    result = evaluate_ragas(
        ticket.incoming_email,
        reply,
        contexts or [],
        transaction,
        policy_store,
        ticket_id=ticket.ticket_id,
        response_id=response_id or ticket.ticket_id,
        cited_rule_ids=cited_rule_ids,
        remedy=remedy,
        reply_source=reply_source,
        category=ticket.category,
        persist=persist,
        cfg=cfg,
    )

    penalty, flags = deterministic_checks(reply, transaction.order_id, policy_store.all_text())
    result.deterministic_penalty = penalty
    result.flags = list(flags)
    if result.gated_from_auto:
        result.flags = result.flags + ["gated_from_auto"]
    if result.retrieval_disagreement is True:
        result.flags = result.flags + ["retrieval_disagreement"]
    if result.scoring_error:
        result.flags = result.flags + [f"scoring_error:{result.scoring_error[:80]}"]

    # Lexical overlap only meaningful when a human actual_reply exists and differs.
    if ticket.actual_reply and ticket.actual_reply != reply:
        result.lexical_overlap = lexical_overlap(reply, ticket.actual_reply)
    else:
        result.lexical_overlap = 0.0

    return result


def evaluate_generated(
    ticket: Ticket,
    transaction: Transaction,
    gen: GeneratedReply,
    policy_store: PolicyStore,
    *,
    persist: bool = True,
) -> EvaluationResult:
    """Convenience wrapper that keeps generation contexts exact for RAGAS."""
    return evaluate_reply(
        ticket,
        transaction,
        gen.reply,
        policy_store,
        "generated",
        retrieved_rule_ids=gen.retrieved_rule_ids,
        cited_rule_ids=gen.cited_rule_ids,
        retrieved_contexts=gen.retrieved_policy_chunks,
        response_id=gen.response_id,
        remedy=gen.remedy,
        persist=persist,
    )


def _demo() -> None:
    penalty, flags = deterministic_checks("short", "ORD-1", "policy text")
    assert penalty >= 4
    assert any("length" in f for f in flags)
    print("evaluator self-check OK")


if __name__ == "__main__":
    _demo()
