"""Routing engine: turn one incoming email into a decision.

  AUTO      high RAGAS quality + clean gates + not escalate
  REVIEW    mid-confidence or gated from auto
  ESCALATE  remedy.escalate OR confidence too low
  IGNORE    classifier category is other (noise)

AUTO is blocked when faithfulness < FAITHFULNESS_GATE, retrieval_disagreement
is true, scoring failed, or deterministic flags exist.
"""

from __future__ import annotations

import hashlib

from src.classifier import classify
from src.company_data.quality import compute_email_quality_flags, rules_index
from src.company_data import routing_metrics
from src.config import DEFAULTS, load_config
from src.evaluator import evaluate_generated
from src.generator import generate_reply
from src.policy_store import PolicyStore
from src.queue_store import DECISION_WEIGHT
from src.ragas_evaluator import check_retrieval_disagreement, should_gate_from_auto
from src.retriever import TicketRetriever
from src.schema import IncomingEmail, Ticket, detect_order_id, placeholder_transaction


def _decide(
    confidence: float,
    flags: list[str],
    escalate: bool,
    gated_from_auto: bool,
    t1: float,
    t2: float,
) -> str:
    """Pure decision boundary — tested directly in _demo(), no LLM involved."""
    if escalate or confidence < t2:
        return "escalate"
    if gated_from_auto or flags:
        return "review"
    if confidence >= t1:
        return "auto"
    return "review"


def _priority(decision: str, price: float, frustrated: bool) -> float:
    value = min(price / 10.0, 30.0)
    return round(DECISION_WEIGHT[decision] + value + (20.0 if frustrated else 0.0), 1)


def _audit_sample(response_id: str, rate: float) -> bool:
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    bucket = int(hashlib.sha256(response_id.encode()).hexdigest(), 16) % 1000
    return bucket < int(rate * 1000)


def route_email(
    email: IncomingEmail,
    transactions: dict,
    policy_store: PolicyStore,
    retriever: TicketRetriever,
    config: dict | None = None,
    *,
    transaction_missing_fields: dict | None = None,
    company_data_version: str = "",
    degraded_bundle: bool = False,
) -> dict:
    cfg = {**DEFAULTS, **load_config(), **(config or {})}
    t1, t2 = float(cfg["t1"]), float(cfg["t2"])
    text = f"{email.subject}\n{email.body}".strip()

    order_id = detect_order_id(text, transactions)
    used_placeholder = not bool(order_id)
    txn = transactions[order_id] if order_id else placeholder_transaction()
    cls = classify(text, has_order=bool(order_id))
    category = cls.category
    frustrated = cls.frustrated

    base = {
        "email_id": email.id,
        "thread_id": email.thread_id,
        "from_addr": email.from_addr,
        "subject": email.subject,
        "body": email.body,
        "order_id": order_id or "",
        "category": category,
        "company_data_version": company_data_version,
        "used_placeholder": used_placeholder,
    }

    if cls.is_noise:
        return {
            **base,
            "decision": "ignore",
            "status": "dismissed",
            "confidence": 0.0,
            "priority": _priority("ignore", txn.price, frustrated),
            "suggested_reply": "",
            "original_reply": "",
            "judge": {},
            "ragas": {},
            "remedy": {},
            "flags": [],
            "response_id": "",
            "audit_sample": False,
        }

    gen = generate_reply(text, txn, policy_store, retriever, category=category)
    live = Ticket(
        ticket_id=email.id,
        order_id=txn.order_id,
        category=category,
        split="holdout",
        sentiment="frustrated" if frustrated else "neutral",
        incoming_email=text,
        actual_reply=gen.reply,
    )
    ev = evaluate_generated(live, txn, gen, policy_store)

    # Live confidence = RAGAS routing quality_score on 0-100, minus deterministic penalty.
    q = float(ev.quality_score) if ev.quality_score is not None else 0.0
    confidence = round(max(0.0, min(100.0, 100.0 * q - ev.deterministic_penalty)), 1)

    # Per-email data-quality flags (never file-wide). Appended to ev.flags.
    missing = None
    if not used_placeholder and order_id and transaction_missing_fields is not None:
        missing = transaction_missing_fields.get(order_id) or frozenset()
    quality_flags = compute_email_quality_flags(
        used_placeholder=used_placeholder,
        order_id=order_id or "",
        missing_fields=missing,
        gen=gen,
        rules_by_id=rules_index(policy_store.rules),
    )
    if quality_flags:
        ev.flags = list(ev.flags) + quality_flags

    # Soft flags that block AUTO via _decide (exclude informational gated_from_auto dup).
    hard_flags = [
        f for f in ev.flags
        if not f.startswith("gated_from_auto") and not f.startswith("scoring_error")
    ]

    # Fail closed on unchecked disagreement: if this would otherwise be AUTO,
    # force the disagreement check before deciding.
    provisional = _decide(
        confidence,
        hard_flags,
        ev.escalate,
        ev.gated_from_auto,
        t1,
        t2,
    )
    if provisional == "auto" and not ev.disagreement_checked:
        topk_rule = gen.remedy.rule_cited or (gen.cited_rule_ids[0] if gen.cited_rule_ids else "")
        disagree = check_retrieval_disagreement(
            text,
            txn,
            policy_store,
            topk_rule,
            sample_rate=1.0,
            response_id=gen.response_id,
            force=True,
        )
        ev.disagreement_checked = bool(disagree.get("disagreement_checked"))
        ev.retrieval_disagreement = disagree.get("retrieval_disagreement")
        ev.topk_rule = disagree.get("topk_rule") or ""
        ev.full_doc_rule = disagree.get("full_doc_rule") or ""
        if not ev.disagreement_checked or ev.retrieval_disagreement is True:
            ev.gated_from_auto = True
            if "retrieval_disagreement" not in ev.flags and ev.retrieval_disagreement is True:
                ev.flags = list(ev.flags) + ["retrieval_disagreement"]
            if not ev.disagreement_checked and "disagreement_unchecked" not in ev.flags:
                ev.flags = list(ev.flags) + ["disagreement_unchecked"]
                hard_flags = [
                    f for f in ev.flags
                    if not f.startswith("gated_from_auto") and not f.startswith("scoring_error")
                ]
        elif should_gate_from_auto(
            float(ev.faithfulness) if ev.faithfulness is not None else None,
            ev.retrieval_disagreement,
            scoring_error=ev.scoring_error or "",
            faithfulness_gate=float(cfg.get("faithfulness_gate", 0.7)),
        ):
            ev.gated_from_auto = True

    decision = _decide(
        confidence,
        hard_flags,
        ev.escalate,
        ev.gated_from_auto,
        t1,
        t2,
    )
    # Final fail-closed: never AUTO without a completed disagreement check.
    if decision == "auto" and not ev.disagreement_checked:
        decision = "review"
        ev.gated_from_auto = True
        if "disagreement_unchecked" not in ev.flags:
            ev.flags = list(ev.flags) + ["disagreement_unchecked"]

    audit = decision == "auto" and _audit_sample(
        gen.response_id, float(cfg.get("audit_sample_rate", 0.05))
    )

    if degraded_bundle:
        routing_metrics.record(decision, list(ev.flags))

    ragas_snap = {
        "faithfulness": ev.faithfulness,
        "answer_relevancy": ev.answer_relevancy,
        "context_precision": ev.context_precision,
        "quality_score": ev.quality_score,
        "retrieval_disagreement": ev.retrieval_disagreement,
        "disagreement_checked": ev.disagreement_checked,
        "gated_from_auto": ev.gated_from_auto,
        "scoring_error": ev.scoring_error,
        "faithfulness_details": ev.faithfulness_details,
    }

    return {
        **base,
        "decision": decision,
        "status": "pending",
        "confidence": confidence,
        "priority": _priority(decision, txn.price, frustrated),
        "suggested_reply": gen.reply,
        "original_reply": gen.reply,
        "response_id": gen.response_id,
        "remedy": gen.remedy.model_dump(),
        "ragas": ragas_snap,
        "judge": {
            # Backward-compatible fields for Review UI during transition.
            "cited_rule": gen.remedy.rule_cited or (gen.cited_rule_ids[0] if gen.cited_rule_ids else ""),
            "escalate": ev.escalate,
            "escalate_reason": ev.escalate_reason,
            "quality_score": ev.quality_score,
            "faithfulness": ev.faithfulness,
        },
        "flags": ev.flags,
        "retrieved_policy_chunks": gen.retrieved_policy_chunks,
        "retrieved_rule_ids": gen.retrieved_rule_ids,
        "cited_rule_ids": gen.cited_rule_ids,
        "retrieved_similar_tickets": gen.retrieved_similar_tickets,
        "audit_sample": audit,
    }


def _demo() -> None:
    assert _decide(95, [], False, False, 80, 50) == "auto"
    assert _decide(30, [], False, False, 80, 50) == "escalate"
    assert _decide(95, [], True, False, 80, 50) == "escalate"
    assert _decide(95, [], False, True, 80, 50) == "review"
    assert _decide(95, ["order_id_not_referenced (-4)"], False, False, 80, 50) == "review"
    assert _decide(65, [], False, False, 80, 50) == "review"
    assert _decide(95, ["unverifiable:order_value"], False, False, 80, 50) == "review"
    assert _priority("escalate", 249, True) > _priority("review", 999, True)
    print("router self-check OK")


if __name__ == "__main__":
    _demo()
