"""Tier-1 RAGAS response-quality scoring (reference-free) + retrieval disagreement.

Uses ragas.metrics.collections.*.ascore(**kwargs) — not the deprecated
SingleTurnSample / single_turn_ascore path.

LLM: ragas.llms.llm_factory for every provider (openai / mistral / anthropic).
Embeddings: embedding_factory('huggingface', ...) for real models; thin
BaseRagasEmbeddings shim only for the offline hash/mock backend.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import types
from datetime import datetime, timezone
from typing import Any

# ragas 0.4.x eagerly imports langchain_community.chat_models.vertexai, which
# was removed from recent langchain-community. Stub it so local eval still loads.
if "langchain_community.chat_models.vertexai" not in sys.modules:
    _vtx = types.ModuleType("langchain_community.chat_models.vertexai")

    class ChatVertexAI:  # noqa: N801 — match upstream name
        pass

    _vtx.ChatVertexAI = ChatVertexAI
    sys.modules["langchain_community.chat_models.vertexai"] = _vtx

from src import llm_client, prompts
from src.config import load_config
from src.policy_store import PolicyStore
from src.schema import EvaluationResult, Remedy, Transaction
from src.storage.factory import get_structured_store

RULE_ID_RE = re.compile(r"\b([A-Z]\d+(?:\.\d+)?|\d+\.\d+)\b", re.IGNORECASE)

# Pinned RAGAS surface: collections + ascore. Bump when re-verified.
RAGAS_PIN = "0.4.3"


def quality_score(faithfulness: float, answer_relevancy: float, context_precision: float) -> float:
    """Routing-only weighted combo (0-1). Never stored as the sole score."""
    return 0.5 * faithfulness + 0.3 * answer_relevancy + 0.2 * context_precision


def should_gate_from_auto(
    faithfulness: float | None,
    retrieval_disagreement: bool | None,
    *,
    scoring_error: str = "",
    faithfulness_gate: float = 0.7,
) -> bool:
    if scoring_error:
        return True
    if faithfulness is None or faithfulness < faithfulness_gate:
        return True
    if retrieval_disagreement is True:
        return True
    return False


def _norm_rule(text: str) -> str:
    m = RULE_ID_RE.search(str(text) or "")
    return m.group(0).upper() if m else str(text).strip().upper()


def _use_hash_embeddings(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return (
        os.getenv("LLM_PROVIDER", "").lower() == "mock"
        or cfg.get("embedding_backend") == "hash"
    )


def _hash_embed(text: str, dim: int = 384) -> list[float]:
    import numpy as np

    v = np.zeros(dim, dtype=np.float32)
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = float(np.linalg.norm(v))
    if n > 0:
        v /= n
    return v.tolist()


def get_ragas_embeddings(cfg: dict | None = None):
    """Native Hugging Face embedding_factory, or hash shim for offline mock."""
    cfg = cfg or load_config()
    model = str(
        cfg.get("embedding_model")
        or os.getenv("EMBEDDING_MODEL")
        or "sentence-transformers/all-MiniLM-L6-v2"
    )
    if _use_hash_embeddings(cfg):
        return _HashRagasEmbeddings()
    from ragas.embeddings.base import embedding_factory

    return embedding_factory("huggingface", model=model)


def get_ragas_llm(purpose: str = "generate"):
    """Native llm_factory for openai / mistral / anthropic. Mock returns None."""
    provider, model = llm_client.resolve_provider_model(purpose)
    if provider == "mock":
        return None
    from ragas.llms import llm_factory

    # Prefer async clients — collections .ascore is async.
    client = llm_client.get_sdk_client(provider, async_client=True)
    # Mistral is OpenAI-compatible; ragas provider stays "openai" with custom client.
    ragas_provider = "openai" if provider == "mistral" else provider
    return llm_factory(model, provider=ragas_provider, client=client)


class _HashRagasEmbeddings:
    """Minimal BaseRagasEmbeddings-compatible shim for offline hash embeddings."""

    def embed_query(self, text: str) -> list[float]:
        return _hash_embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embed(t) for t in texts]

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def embed_text(self, text: str, is_async: bool = True) -> list[float]:
        return self.embed_query(text)

    async def embed_texts(self, texts: list[str], is_async: bool = True) -> list[list[float]]:
        return self.embed_documents(texts)


def _mock_ragas_scores(user_input: str, response: str, retrieved_contexts: list[str]) -> dict:
    """Deterministic offline scores so plumbing works without RAGAS/network."""
    seed = int(hashlib.sha256((user_input + response).encode()).hexdigest(), 16)
    faith = 0.55 + (seed % 40) / 100.0  # 0.55-0.94
    relev = 0.50 + ((seed // 7) % 45) / 100.0
    prec = 0.40 + ((seed // 13) % 50) / 100.0
    return {
        "faithfulness": round(min(faith, 1.0), 4),
        "answer_relevancy": round(min(relev, 1.0), 4),
        "context_precision": round(min(prec, 1.0), 4),
        "faithfulness_details": {
            "unsupported_claims": [],
            "note": "mock ragas scores (no API / offline)",
            "n_contexts": len(retrieved_contexts),
        },
        "scoring_error": "",
    }


async def _ascore_collections(
    user_input: str,
    response: str,
    retrieved_contexts: list[str],
    llm,
    embeddings,
) -> dict:
    from ragas.metrics.collections import AnswerRelevancy, Faithfulness
    from ragas.metrics.collections.context_precision import ContextPrecisionWithoutReference

    faith_m = Faithfulness(llm=llm)
    relev_m = AnswerRelevancy(llm=llm, embeddings=embeddings)
    prec_m = ContextPrecisionWithoutReference(llm=llm)

    kwargs = {
        "user_input": user_input,
        "response": response,
        "retrieved_contexts": retrieved_contexts,
    }
    faith_r, relev_r, prec_r = await asyncio.gather(
        faith_m.ascore(**kwargs),
        relev_m.ascore(**kwargs),
        prec_m.ascore(**kwargs),
    )

    def _val(r) -> float:
        if hasattr(r, "value"):
            return float(r.value)
        return float(r)

    details: dict[str, Any] = {}
    for attr in ("statements", "claims", "reason", "details"):
        if hasattr(faith_r, attr):
            try:
                details[attr] = getattr(faith_r, attr)
            except Exception:
                pass

    return {
        "faithfulness": _val(faith_r),
        "answer_relevancy": _val(relev_r),
        "context_precision": _val(prec_r),
        "faithfulness_details": details,
        "scoring_error": "",
    }


def score_response(
    user_input: str,
    response: str,
    retrieved_contexts: list[str],
    *,
    cfg: dict | None = None,
) -> dict:
    """Run the three reference-free RAGAS metrics. Returns a structured dict."""
    cfg = cfg or load_config()
    provider, _ = llm_client.resolve_provider_model("generate")
    if provider == "mock":
        return _mock_ragas_scores(user_input, response, retrieved_contexts)

    try:
        llm = get_ragas_llm("generate")
        embeddings = get_ragas_embeddings(cfg)
        return asyncio.run(
            _ascore_collections(user_input, response, retrieved_contexts, llm, embeddings)
        )
    except Exception as exc:
        return {
            "faithfulness": None,
            "answer_relevancy": None,
            "context_precision": None,
            "faithfulness_details": {},
            "scoring_error": f"{type(exc).__name__}: {exc}",
        }


def check_retrieval_disagreement(
    email: str,
    transaction: Transaction,
    policy_store: PolicyStore,
    topk_cited_rule: str,
    *,
    sample_rate: float = 0.1,
    response_id: str = "",
    force: bool = False,
) -> dict:
    """Label-free dual-pass: top-k cited rule vs full-document rule extraction.

    Returns disagreement_checked, retrieval_disagreement (nullable), rules.
    """
    if not force:
        # Deterministic sample from response_id / email hash.
        key = response_id or email
        bucket = int(hashlib.sha256(key.encode()).hexdigest(), 16) % 1000
        if bucket >= int(max(0.0, min(1.0, sample_rate)) * 1000):
            return {
                "disagreement_checked": False,
                "retrieval_disagreement": None,
                "topk_rule": _norm_rule(topk_cited_rule),
                "full_doc_rule": "",
            }

    policy_text = policy_store.all_text()
    # Hierarchical multi-pass when the document is large.
    if len(policy_text) > 12000:
        chunks = [r.text for r in policy_store.rules]
        mid = max(1, len(chunks) // 2)
        parts = ["\n\n".join(chunks[:mid]), "\n\n".join(chunks[mid:])]
        rules_found = []
        for part in parts:
            raw = llm_client.complete(
                prompts.FULL_POLICY_RULE_SYSTEM,
                prompts.FULL_POLICY_RULE_USER.format(
                    policy_text=part,
                    transaction=json.dumps(transaction.model_dump(), indent=2),
                    email=email,
                ),
                max_tokens=200,
            )
            try:
                data = llm_client.extract_json(raw)
                rules_found.append(_norm_rule(data.get("rule", "")))
            except Exception:
                pass
        # Second pass: pick among candidates with a shortlist.
        shortlist = "\n".join(
            r.text for r in policy_store.rules if _norm_rule(r.id) in rules_found
        ) or policy_text[:12000]
        policy_text = shortlist

    raw = llm_client.complete(
        prompts.FULL_POLICY_RULE_SYSTEM,
        prompts.FULL_POLICY_RULE_USER.format(
            policy_text=policy_text[:12000],
            transaction=json.dumps(transaction.model_dump(), indent=2),
            email=email,
        ),
        max_tokens=200,
    )
    full_rule = ""
    try:
        data = llm_client.extract_json(raw)
        full_rule = _norm_rule(data.get("rule", ""))
    except Exception:
        full_rule = _norm_rule(raw)

    topk = _norm_rule(topk_cited_rule)
    disagree = bool(topk and full_rule and topk != full_rule)
    return {
        "disagreement_checked": True,
        "retrieval_disagreement": disagree,
        "topk_rule": topk,
        "full_doc_rule": full_rule,
    }


def persist_ragas_scores(result: EvaluationResult) -> str:
    """Write one ragas_scores row via the pluggable structured store."""
    store = get_structured_store()
    rid = result.response_id or result.ticket_id
    record = {
        "id": rid,
        "response_id": rid,
        "ticket_id": result.ticket_id,
        "faithfulness": result.faithfulness,
        "answer_relevancy": result.answer_relevancy,
        "context_precision": result.context_precision,
        "retrieval_disagreement": result.retrieval_disagreement,
        "disagreement_checked": result.disagreement_checked,
        "quality_score": result.quality_score,
        "gated_from_auto": result.gated_from_auto,
        "faithfulness_details": result.faithfulness_details,
        "scoring_error": result.scoring_error,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return store.insert("ragas_scores", record)


def evaluate_ragas(
    email: str,
    reply: str,
    retrieved_contexts: list[str],
    transaction: Transaction,
    policy_store: PolicyStore,
    *,
    ticket_id: str = "",
    response_id: str = "",
    cited_rule_ids: list[str] | None = None,
    remedy: Remedy | None = None,
    reply_source: str = "generated",
    category: str = "",
    persist: bool = True,
    cfg: dict | None = None,
) -> EvaluationResult:
    """Full Tier-1 evaluation path used by evaluator.py / router."""
    cfg = cfg or load_config()
    gate = float(cfg.get("faithfulness_gate", 0.7))
    sample_rate = float(cfg.get("retrieval_disagreement_sample_rate", 0.1))

    scores = score_response(email, reply, retrieved_contexts, cfg=cfg)
    faith = scores.get("faithfulness")
    relev = scores.get("answer_relevancy")
    prec = scores.get("context_precision")
    err = scores.get("scoring_error") or ""

    q = None
    if faith is not None and relev is not None and prec is not None:
        q = round(quality_score(float(faith), float(relev), float(prec)), 4)

    topk_rule = ""
    if cited_rule_ids:
        topk_rule = cited_rule_ids[0]
    elif remedy and remedy.rule_cited:
        topk_rule = remedy.rule_cited

    disagree = check_retrieval_disagreement(
        email,
        transaction,
        policy_store,
        topk_rule,
        sample_rate=sample_rate,
        response_id=response_id or ticket_id,
    )

    gated = should_gate_from_auto(
        float(faith) if faith is not None else None,
        disagree.get("retrieval_disagreement"),
        scoring_error=err,
        faithfulness_gate=gate,
    )

    rem = remedy or Remedy()
    result = EvaluationResult(
        ticket_id=ticket_id,
        response_id=response_id or ticket_id,
        reply_source=reply_source,
        category=category,
        faithfulness=faith,
        answer_relevancy=relev,
        context_precision=prec,
        quality_score=q,
        faithfulness_details=scores.get("faithfulness_details") or {},
        disagreement_checked=bool(disagree.get("disagreement_checked")),
        retrieval_disagreement=disagree.get("retrieval_disagreement"),
        topk_rule=disagree.get("topk_rule") or "",
        full_doc_rule=disagree.get("full_doc_rule") or "",
        gated_from_auto=gated,
        scoring_error=err,
        remedy=rem,
        escalate=bool(rem.escalate),
        escalate_reason="remedy.escalate from generator" if rem.escalate else "",
    )
    if persist:
        try:
            persist_ragas_scores(result)
        except Exception:
            pass
    return result


def _demo() -> None:
    # Gate logic
    assert should_gate_from_auto(0.9, False) is False
    assert should_gate_from_auto(0.5, False) is True
    assert should_gate_from_auto(0.9, True) is True
    assert should_gate_from_auto(0.9, None, scoring_error="boom") is True
    assert abs(quality_score(1.0, 1.0, 1.0) - 1.0) < 1e-9

    # Mock scoring path
    os.environ["LLM_PROVIDER"] = "mock"
    scores = score_response("I want a refund", "We will refund per R1.1", ["R1.1 full refund"])
    assert scores["faithfulness"] is not None
    assert not scores["scoring_error"]

    # Hash embeddings shim
    emb = get_ragas_embeddings({"embedding_backend": "hash"})
    assert len(emb.embed_query("hello")) == 384

    # Disagreement sampling skip
    d = check_retrieval_disagreement(
        "email",
        Transaction(
            order_id="X", customer_id="c", product="p", price=1.0,
            order_date="2026-01-01", status="delivered",
        ),
        PolicyStore("data/policy.pdf", config={"embedding_backend": "hash", "use_embeddings": False}),
        "R1.1",
        sample_rate=0.0,
        response_id="never-sample",
    )
    assert d["disagreement_checked"] is False
    assert d["retrieval_disagreement"] is None

    # API contract: no SingleTurnSample in this module's public scoring path
    import inspect
    src = inspect.getsource(score_response) + inspect.getsource(_ascore_collections)
    assert "SingleTurnSample" not in src
    assert "single_turn_ascore" not in src
    print("ragas_evaluator self-check OK")


if __name__ == "__main__":
    _demo()
