"""Classification stage: LLM categorizes a parsed email before generation.

Categories (slugs): refund | cancellation | complain | billing |
technical_support | general_inquiry | other

`other` is the noise gate — router IGNORE without drafting a reply.
Frustration stays a cheap regex (priority boost only; not an LLM field).

Provider/model come from CLASSIFY_LLM_* (Settings → Email categorization),
falling back to LLM_* via llm_client.complete(purpose="classify").
"""

from __future__ import annotations

import os
import re

from pydantic import BaseModel

from src import llm_client, prompts

CATEGORIES = (
    "refund",
    "cancellation",
    "complain",
    "billing",
    "technical_support",
    "general_inquiry",
    "other",
)

# Human labels shown in Settings / docs — keep in sync with CATEGORIES.
CATEGORY_LABELS = {
    "refund": "Refund",
    "cancellation": "Cancellation",
    "complain": "Complain",
    "billing": "Billing",
    "technical_support": "Technical support",
    "general_inquiry": "General inquiry",
    "other": "Other (noise)",
}

_ALIASES = {
    "refund": "refund",
    "return": "refund",
    "cancellation": "cancellation",
    "cancel": "cancellation",
    "complain": "complain",
    "complaint": "complain",
    "billing": "billing",
    "technical support": "technical_support",
    "technical_support": "technical_support",
    "tech support": "technical_support",
    "general inquiry": "general_inquiry",
    "general_inquiry": "general_inquiry",
    "inquiry": "general_inquiry",
    "other": "other",
    "other (noise)": "other",
    "noise": "other",
}

_FRUSTRATED_RE = re.compile(
    r"!!!|unacceptable|furious|ridiculous|outrage|terrible|angry|worst|asap|immediately|right now",
    re.IGNORECASE,
)


class ClassificationResult(BaseModel):
    category: str
    is_noise: bool
    frustrated: bool


def normalize_category(raw: str) -> str:
    s = re.sub(r"[\s\-]+", " ", (raw or "").strip().lower().strip("\"'"))
    if s in _ALIASES:
        return _ALIASES[s]
    underscored = s.replace(" ", "_")
    if underscored in CATEGORIES:
        return underscored
    if underscored in _ALIASES:
        return _ALIASES[underscored]
    return "general_inquiry"


def is_frustrated(text: str) -> bool:
    return bool(_FRUSTRATED_RE.search(text))


def classify(text: str, has_order: bool = False) -> ClassificationResult:
    """Categorize email via the classify-purpose LLM. `other` => noise/IGNORE.

    If an order id is present, never treat as noise — remap other → general_inquiry
    so real order mail isn't dismissed.
    """
    raw = llm_client.complete(
        prompts.CLASSIFIER_SYSTEM,
        prompts.CLASSIFIER_USER.format(email=text),
        max_tokens=64,
        purpose="classify",
    )
    try:
        data = llm_client.extract_json(raw)
        category = normalize_category(str(data.get("category", "")))
    except (ValueError, TypeError, AttributeError):
        # Bare slug / label fallback if the model skipped JSON
        category = normalize_category(raw.strip().splitlines()[0] if raw.strip() else "")

    if category == "other" and has_order:
        category = "general_inquiry"

    return ClassificationResult(
        category=category,
        is_noise=(category == "other"),
        frustrated=is_frustrated(text),
    )


def _demo() -> None:
    """Offline self-check — uses mock LLM (no API key)."""
    os.environ["CLASSIFY_LLM_PROVIDER"] = "mock"
    os.environ.setdefault("LLM_PROVIDER", "mock")

    assert normalize_category("Refund") == "refund"
    assert normalize_category("Technical support") == "technical_support"
    assert normalize_category("Other (noise)") == "other"
    assert normalize_category("complaint") == "complain"

    r = classify("I want a refund for my order, this is ridiculous", has_order=True)
    assert r.category == "refund" and not r.is_noise and r.frustrated

    noise = classify("Big summer sale! unsubscribe here", has_order=False)
    assert noise.category == "other" and noise.is_noise

    # Order id present → never ignore even if model says other
    kept = classify("unsubscribe from deals about order ORD-1", has_order=True)
    assert kept.category == "general_inquiry" and not kept.is_noise

    assert is_frustrated("this is UNACCEPTABLE, I need this fixed ASAP")
    assert not is_frustrated("could you help when you get a chance")
    print("classifier self-check OK")


if __name__ == "__main__":
    _demo()
