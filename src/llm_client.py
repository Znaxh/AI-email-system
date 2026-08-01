"""Single pluggable LLM interface used by generation, classification, and judges.

Provider is selected with env vars (Settings writes these into .env):
  - LLM_PROVIDER / LLM_MODEL                 → email generation (+ evaluation judges)
  - CLASSIFY_LLM_PROVIDER / CLASSIFY_LLM_MODEL → email categorization
    (falls back to LLM_* when unset)

Providers:
  - "anthropic" (default) -> Claude via the official anthropic SDK
  - "openai"              -> OpenAI via the official openai SDK
  - "mistral"             -> Mistral via its OpenAI-compatible endpoint
                             (free tier friendly: throttled to ~1 req/s)
  - "mock"                -> deterministic offline stub, used only to smoke-test
                             the pipeline plumbing without an API key. Not part
                             of the graded flow; results are meaningless.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o",
    "mistral": "mistral-small-latest",
}

_last_mistral_call = 0.0
_client_cache: dict[str, object] = {}


def resolve_provider_model(purpose: str = "generate") -> tuple[str, str]:
    """Return (provider, model) for a pipeline step. Classify falls back to generate."""
    if purpose == "classify":
        provider = (os.getenv("CLASSIFY_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "anthropic").lower()
        if os.getenv("CLASSIFY_LLM_MODEL"):
            model = os.environ["CLASSIFY_LLM_MODEL"]
        elif os.getenv("CLASSIFY_LLM_PROVIDER"):
            model = DEFAULT_MODELS.get(provider, "")
        else:
            model = os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(provider, "")
        return provider, model

    provider = (os.getenv("LLM_PROVIDER") or "anthropic").lower()
    model = os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(provider, "")
    return provider, model


def get_sdk_client(provider: str | None = None, *, async_client: bool = False):
    """Reusable provider SDK client for RAGAS llm_factory and complete()."""
    provider = (provider or resolve_provider_model("generate")[0]).lower()
    cache_key = f"{provider}:{'async' if async_client else 'sync'}"
    if cache_key in _client_cache:
        return _client_cache[cache_key]

    if provider == "anthropic":
        import anthropic

        client = anthropic.AsyncAnthropic() if async_client else anthropic.Anthropic()
    elif provider == "openai":
        from openai import AsyncOpenAI, OpenAI

        client = AsyncOpenAI() if async_client else OpenAI()
    elif provider == "mistral":
        from openai import AsyncOpenAI, OpenAI

        kwargs = {
            "base_url": "https://api.mistral.ai/v1",
            "api_key": os.environ["MISTRAL_API_KEY"],
            "max_retries": 8,
        }
        client = AsyncOpenAI(**kwargs) if async_client else OpenAI(**kwargs)
    elif provider == "mock":
        client = None
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")

    _client_cache[cache_key] = client
    return client


def complete(
    system: str,
    user: str,
    max_tokens: int = 1200,
    *,
    purpose: str = "generate",
) -> str:
    """One-shot completion. Same signature for every provider."""
    provider, model = resolve_provider_model(purpose)

    if provider == "anthropic":
        client = get_sdk_client("anthropic")
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in response.content if b.type == "text")

    if provider in ("openai", "mistral"):
        if provider == "mistral":
            global _last_mistral_call
            wait = 1.1 - (time.monotonic() - _last_mistral_call)
            if wait > 0:
                time.sleep(wait)
            _last_mistral_call = time.monotonic()
        client = get_sdk_client(provider)
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""

    if provider == "mock":
        return _mock_complete(system, user)

    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (use anthropic|openai|mistral)")


def extract_json(text: str) -> dict:
    """Robustly pull the first JSON object out of an LLM response
    (handles markdown fences and surrounding prose)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object in LLM output: {text[:200]}")
    # walk to the matching closing brace
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"Unbalanced JSON in LLM output: {text[:200]}")


def _mock_complete(system: str, user: str) -> str:
    """Deterministic stub for offline plumbing tests only."""
    seed = int(hashlib.sha256(user.encode()).hexdigest(), 16)
    sys_l = system.lower()
    # Intent extraction — pick a category from the listed ones.
    if "retrieval intent" in sys_l or "coarse retrieval intent" in sys_l:
        low = user.lower()
        cat = "global"
        if any(h in low for h in ("return", "refund")):
            cat = "returns"
        elif any(h in low for h in ("ship", "package", "delivery", "damaged", "lost")):
            cat = "shipping"
        elif "cancel" in low:
            cat = "cancellation"
        elif any(h in low for h in ("warranty", "defect", "broken")):
            cat = "warranty"
        elif any(h in low for h in ("charge", "billing", "invoice", "duplicate")):
            cat = "billing"
        return json.dumps({"category": cat, "region": "", "as_of": "", "entities": {}})
    # Policy segment / normalize helpers
    if "split an unstructured" in sys_l:
        return json.dumps({"sections": [{"heading": "R0 Mock", "body": "Mock body MUST apply."}]})
    if "normalize one policy section" in sys_l:
        return json.dumps({
            "id": "R0",
            "condition": "mock condition",
            "outcome": "MUST apply mock remedy",
            "category": "global",
            "region": "",
            "effective_date": "",
        })
    # Classifier prompt — return a category JSON so routing self-checks work offline.
    if "categor" in sys_l and "refund" in sys_l:
        low = user.lower()
        if any(h in low for h in ("unsubscribe", "newsletter", "promotion", "sale")):
            cat = "other"
        elif any(h in low for h in ("cancel",)):
            cat = "cancellation"
        elif any(h in low for h in ("charge", "billing", "invoice", "duplicate")):
            cat = "billing"
        elif any(h in low for h in ("refund", "return", "money back")):
            cat = "refund"
        elif any(h in low for h in ("broken", "defect", "warranty", "not working", "error")):
            cat = "technical_support"
        elif any(h in low for h in ("complain", "unacceptable", "furious", "terrible", "angry")):
            cat = "complain"
        elif any(h in low for h in ("help", "question", "how do", "wondering")):
            cat = "general_inquiry"
        else:
            cat = "general_inquiry"
        return json.dumps({"category": cat})
    # Generator — JSON with reply + cited_rules + remedy
    if "drafting a reply" in sys_l or "cited_rules" in sys_l:
        m = re.search(r"\b(R\d+(?:\.\d+)?)\b", user)
        rid = m.group(1) if m else "R0"
        oid_m = re.search(r'"order_id":\s*"([^"]*)"', user)
        oid = oid_m.group(1) if oid_m else "ORDER"
        reply = (
            f"Hi, thanks for reaching out about your order {oid}. Based on our policy "
            f"(per {rid}) I've reviewed your request and here is the outcome, along with "
            "the next steps we will take to resolve it. (mock reply generated offline "
            "without an API key; set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env for real "
            "output)"
        )
        return json.dumps({
            "reply": reply,
            "cited_rules": [rid],
            "remedy": {
                "remedy_type": "refund",
                "remedy_amount": None,
                "rule_cited": rid,
                "escalate": False,
            },
        })
    # Remedy extraction / full-policy rule selection
    if "extract the structured remedy" in sys_l:
        m = re.search(r"\b(R\d+(?:\.\d+)?)\b", user)
        rid = m.group(1) if m else ""
        return json.dumps({
            "remedy_type": "refund",
            "remedy_amount": None,
            "rule_cited": rid,
            "escalate": False,
        })
    if "select the single governing policy rule" in sys_l:
        m = re.search(r"\b(R\d+(?:\.\d+)?)\b", user)
        rid = m.group(1) if m else "R0"
        return json.dumps({"rule": rid, "escalate": False})
    if "JSON" in system or "json" in system:
        return json.dumps({"rule": "R0", "escalate": False})
    return (
        "Hi, thanks for reaching out about your order. Based on our policy I've "
        "reviewed your request and here is the outcome, along with the next steps "
        "we will take to resolve it. (mock reply generated offline without an API "
        "key; set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env for real output) "
        "— Support"
    )
