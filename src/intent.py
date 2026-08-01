"""Coarse intent + entity extraction for scoped policy retrieval.

Categories are taken from the loaded policy document (dynamic), not a fixed
company taxonomy. Cross-cutting `global` rules are always searched alongside
the selected category.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src import llm_client, prompts


@dataclass
class IntentResult:
    category: str = "global"
    entities: dict = field(default_factory=dict)
    region: str = ""
    as_of: str = ""


def extract_intent(
    email: str,
    categories: list[str],
    *,
    transaction: dict | None = None,
) -> IntentResult:
    """Tag the email's category + light entities for retrieval scoping."""
    cats = sorted({c for c in categories if c and c != "global"}) or ["general"]
    txn = transaction or {}
    try:
        raw = llm_client.complete(
            prompts.INTENT_SYSTEM.format(categories=", ".join(cats + ["global"])),
            prompts.INTENT_USER.format(email=email),
            max_tokens=300,
            purpose="classify",
        )
        data = llm_client.extract_json(raw)
    except Exception:
        data = _heuristic_intent(email, cats)

    category = str(data.get("category") or "global").strip().lower().replace(" ", "_")
    if category not in cats and category != "global":
        # Fuzzy: if model returned a close label, map; else global (still gets all globals).
        category = _closest(category, cats) or "global"

    entities = data.get("entities") if isinstance(data.get("entities"), dict) else {}
    region = str(data.get("region") or entities.get("region") or "").strip()
    as_of = str(data.get("as_of") or entities.get("date") or "").strip()

    # Fill gaps from the transaction record when the email omitted them.
    if not region and txn.get("region"):
        region = str(txn["region"])
    if not as_of:
        as_of = str(txn.get("order_date") or txn.get("delivery_date") or "")

    return IntentResult(category=category, entities=entities, region=region, as_of=as_of)


def _closest(label: str, cats: list[str]) -> str:
    if label in cats:
        return label
    for c in cats:
        if label in c or c in label:
            return c
    return ""


def _heuristic_intent(email: str, cats: list[str]) -> dict:
    low = email.lower()
    mapping = [
        ("returns", ("return", "refund", "store credit")),
        ("shipping", ("shipping", "delivery", "package", "lost", "damaged")),
        ("cancellation", ("cancel",)),
        ("warranty", ("warranty", "defect", "broken")),
        ("billing", ("charge", "billing", "invoice", "duplicate", "price match")),
    ]
    for cat, keys in mapping:
        if cat in cats and any(k in low for k in keys):
            return {"category": cat, "entities": {}, "region": "", "as_of": ""}
    return {"category": "global", "entities": {}, "region": "", "as_of": ""}


def _demo() -> None:
    # Force heuristic path (no API) by calling it directly.
    data = _heuristic_intent("I'd like to return my jacket for a refund", ["returns", "shipping"])
    assert data["category"] == "returns"
    print("intent self-check OK")


if __name__ == "__main__":
    _demo()
