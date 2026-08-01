"""Shared data models. Everything here is company-agnostic: the fields describe
generic e-commerce support concepts (orders, tickets, replies, scores), not any
specific company's policy."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class Transaction(BaseModel):
    """A generic customer transaction record. Any company's order export can be
    mapped onto this shape. Extra fields are allowed so a different company's
    export can carry additional columns without code changes.

    Recommended fields (customer_id, order_date, price, status) may be missing
    after a DEGRADED upload; missingness is recorded separately
    (`_missing_fields` / bundle `transaction_missing_fields`) and must never be
    inferred from sentinel defaults such as price=0.0.
    """

    model_config = {"extra": "allow"}

    order_id: str
    customer_id: str = ""
    product: str = "(unspecified product)"
    price: float = 0.0
    order_date: str = ""
    delivery_date: Optional[str] = None
    promised_delivery_date: Optional[str] = None
    status: str = ""
    final_sale: bool = False
    returns_last_90_days: int = 0


class Ticket(BaseModel):
    """One historical support interaction: the incoming email and the reply a
    human agent actually sent."""

    ticket_id: str
    order_id: str
    category: str
    split: str  # "corpus" (retrieval pool) or "holdout" (test set)
    sentiment: str = "neutral"
    incoming_email: str
    actual_reply: str


class IncomingEmail(BaseModel):
    """A live inbound email fetched from a connected inbox (or the demo inbox)."""

    id: str
    thread_id: str = ""
    from_addr: str = ""
    subject: str = ""
    body: str = ""
    received_at: str = ""
    raw_headers: dict = Field(default_factory=dict)
    auth_status: str = "unavailable"  # pass | fail | unavailable
    auth_detail: str = ""
    parse_flags: list[str] = Field(default_factory=list)


class PolicyRule(BaseModel):
    """One normalized policy rule extracted from a company document."""

    id: str
    condition: str = ""
    outcome: str = ""
    category: str = "global"
    region: str = ""
    effective_date: str = ""
    text: str = ""
    section_hash: str = ""
    # Transaction fields this rule's conditions depend on. Populated at ingest.
    # dependency_status="unknown" means deterministic inference could not decide.
    depends_on: list[str] = Field(default_factory=list)
    dependency_status: str = "resolved"  # resolved | unknown


class Remedy(BaseModel):
    """Structured remedy extracted alongside a free-text reply.

    Used for deterministic edit classification (minor vs major) — not judgment.
    """

    model_config = {"extra": "allow"}

    remedy_type: str = ""
    remedy_amount: Optional[float] = None
    rule_cited: str = ""
    escalate: bool = False


class GeneratedReply(BaseModel):
    ticket_id: str
    response_id: str = ""
    reply: str
    remedy: Remedy = Field(default_factory=Remedy)
    retrieved_policy_chunks: list[str] = Field(default_factory=list)
    retrieved_rule_ids: list[str] = Field(default_factory=list)
    cited_rule_ids: list[str] = Field(default_factory=list)
    retrieved_similar_tickets: list[str] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    """RAGAS response-quality scores + deterministic diagnostics for one reply.

    The three RAGAS metrics and the routing-only quality_score are stored
    separately — never blended with human-feedback reliability.
    """

    ticket_id: str
    response_id: str = ""
    reply_source: str = "generated"  # generated | live
    category: str = ""

    # Tier-1 RAGAS (0-1 each; never blended with Tier-2)
    faithfulness: Optional[float] = None
    answer_relevancy: Optional[float] = None
    context_precision: Optional[float] = None
    quality_score: Optional[float] = None  # routing-only weighted combo
    faithfulness_details: dict[str, Any] = Field(default_factory=dict)

    # Dual-pass retrieval consistency
    disagreement_checked: bool = False
    retrieval_disagreement: Optional[bool] = None
    topk_rule: str = ""
    full_doc_rule: str = ""

    # Gates
    gated_from_auto: bool = False
    scoring_error: str = ""

    # Deterministic diagnostics (non-blended)
    deterministic_penalty: int = 0
    flags: list[str] = Field(default_factory=list)
    lexical_overlap: float = 0.0

    # Structured remedy from generation (for routing escalate)
    remedy: Remedy = Field(default_factory=Remedy)
    escalate: bool = False
    escalate_reason: str = ""


def detect_order_id(email: str, order_ids) -> Optional[str]:
    """First known order id that appears verbatim (case-insensitive) in the email."""
    lower = email.lower()
    hits = [(lower.find(oid.lower()), oid) for oid in order_ids if oid.lower() in lower]
    return min(hits)[1] if hits else None


def placeholder_transaction() -> "Transaction":
    """Neutral stand-in when an email matches no known order."""
    return Transaction(
        order_id="",
        customer_id="",
        product="(no transaction record on file)",
        price=0.0,
        order_date="",
        status="unknown",
    )
