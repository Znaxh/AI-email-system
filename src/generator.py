"""Reply generator: RAG over the policy document + similar past tickets.

Past tickets teach voice/tone only and must never override policy.
Emits structured remedy alongside the free-text reply for deterministic
edit classification downstream.
"""

from __future__ import annotations

import json
import re
import uuid

from src import llm_client, prompts
from src.config import load_config
from src.intent import extract_intent
from src.policy_store import PolicyStore
from src.retriever import TicketRetriever
from src.schema import GeneratedReply, Remedy, Transaction

RULE_ID_RE = re.compile(r"\b([A-Z]\d+(?:\.\d+)?|\d+\.\d+)\b")

_CLASSIFIER_TO_POLICY = {
    "refund": "returns",
    "cancellation": "cancellation",
    "billing": "billing",
    "technical_support": "warranty",
    "complain": "global",
    "general_inquiry": "global",
    "other": "global",
}

_PREFIX_TO_POLICY = (
    ("return", "returns"),
    ("shipping", "shipping"),
    ("cancel", "cancellation"),
    ("warranty", "warranty"),
    ("duplicate_charge", "billing"),
    ("price_match", "billing"),
    ("lost_package", "shipping"),
    ("damaged", "shipping"),
    ("escalation", "global"),
)


def _map_to_policy_category(raw: str | None, policy_cats: set[str]) -> str | None:
    if not raw:
        return None
    cat = raw.strip().lower().replace(" ", "_")
    if cat in _CLASSIFIER_TO_POLICY:
        cat = _CLASSIFIER_TO_POLICY[cat]
    if cat in policy_cats or cat == "global":
        return cat
    for c in policy_cats:
        if cat.startswith(c) or c in cat:
            return c
    for prefix, mapped in _PREFIX_TO_POLICY:
        if cat.startswith(prefix) or prefix in cat:
            return mapped if mapped in policy_cats or mapped == "global" else mapped
    return None


def generate_reply(
    email: str,
    transaction: Transaction,
    policy_store: PolicyStore,
    retriever: TicketRetriever,
    ticket_id: str = "",
    k_policy: int | None = None,
    k_tickets: int = 3,
    category: str | None = None,
) -> GeneratedReply:
    cfg = load_config()
    k_policy = k_policy if k_policy is not None else int(cfg.get("k_policy", 4))

    policy_cats = set(policy_store.categories())
    intent_cat = _map_to_policy_category(category, policy_cats)
    region = ""
    as_of = ""

    if not intent_cat:
        intent = extract_intent(
            email,
            policy_store.categories(),
            transaction=transaction.model_dump(),
        )
        intent_cat = intent.category
        region = intent.region
        as_of = intent.as_of

    query = f"{email}\n{transaction.status} {transaction.product}"
    rules = policy_store.retrieve_rules(
        query,
        k=k_policy,
        category=intent_cat,
        region=region or None,
        as_of=as_of or None,
    )
    policy_chunks = [r.text for r in rules]
    rule_ids = [r.id for r in rules]
    similar = retriever.top_k(email, k=k_tickets)

    examples = "\n\n".join(
        f"Customer: {t.incoming_email}\nAgent: {t.actual_reply}" for t in similar
    )
    user_prompt = prompts.GENERATOR_USER.format(
        policy_chunks="\n\n".join(policy_chunks) or "(no policy excerpts retrieved)",
        transaction=json.dumps(transaction.model_dump(), indent=2),
        examples=examples or "(no similar past tickets)",
        email=email,
    )
    raw = llm_client.complete(
        prompts.GENERATOR_SYSTEM, user_prompt, purpose="generate"
    ).strip()

    reply, cited, remedy = _parse_generation(raw, rule_ids)
    response_id = f"{ticket_id or 'live'}-{uuid.uuid4().hex[:12]}"
    return GeneratedReply(
        ticket_id=ticket_id,
        response_id=response_id,
        reply=reply,
        remedy=remedy,
        retrieved_policy_chunks=policy_chunks,
        retrieved_rule_ids=rule_ids,
        cited_rule_ids=cited,
        retrieved_similar_tickets=[t.ticket_id for t in similar],
    )


def _parse_generation(raw: str, retrieved_ids: list[str]) -> tuple[str, list[str], Remedy]:
    cited: list[str] = []
    reply = raw
    remedy = Remedy()
    try:
        data = llm_client.extract_json(raw)
        if isinstance(data, dict) and data.get("reply"):
            reply = str(data["reply"]).strip()
            raw_cited = data.get("cited_rules") or data.get("cited_rule_ids") or []
            if isinstance(raw_cited, str):
                raw_cited = [raw_cited]
            cited = [_norm_rule(c) for c in raw_cited if str(c).strip()]
            rem = data.get("remedy") or {}
            if isinstance(rem, dict):
                amount = rem.get("remedy_amount")
                try:
                    amount = float(amount) if amount is not None and amount != "" else None
                except (TypeError, ValueError):
                    amount = None
                remedy = Remedy(
                    remedy_type=str(rem.get("remedy_type") or ""),
                    remedy_amount=amount,
                    rule_cited=_norm_rule(rem.get("rule_cited") or ""),
                    escalate=bool(rem.get("escalate")),
                )
    except Exception:
        reply = raw.strip()

    if not cited:
        cited = [_norm_rule(m) for m in RULE_ID_RE.findall(reply)]
    seen = set()
    ordered = []
    for c in cited:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)
    if not remedy.rule_cited and ordered:
        remedy.rule_cited = ordered[0]
    return reply, ordered, remedy


def _norm_rule(text: str) -> str:
    m = RULE_ID_RE.search(str(text) or "")
    return m.group(0).upper() if m else str(text).strip().upper()
