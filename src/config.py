"""Non-secret runtime configuration (thresholds, connector choice, notification
settings) persisted to config.json. Secrets (API keys, MCP token) never live
here — they stay in .env via views/common.py::update_env.

Company-agnostic: these are product settings, not company facts.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path("config.json")

DEFAULTS: dict = {
    "email_source": "demo",  # demo (offline, empty inbox) | mcp
    # routing thresholds on the 0-100 live-confidence scale
    "t1": 80.0,  # >= t1 (and clean) -> auto-reply
    "t2": 50.0,  # <  t2            -> escalate
    "live_send": False,  # dry-run by default; nothing is actually sent until True
    "digest_enabled": False,
    "digest_recipient": "",
    # MCP tool-name overrides (blank -> auto-map by capability). Defaults match
    # common Gmail MCP servers; override per-server if names differ.
    "mcp_tool_search": "",
    "mcp_tool_get": "",
    "mcp_tool_send": "",
    # Policy retrieval (non-secret). Company document path is under data/.
    "policy_filename": "policy.pdf",
    "use_embeddings": True,
    "use_bm25": True,
    "rrf_k": 60,
    "cross_encoder_rerank": False,
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
    "cross_encoder_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "policy_llm_chunking": False,  # LLM fallback for unstructured sections
    "k_policy": 4,
    # Evaluation v2 — RAGAS gates + human-feedback sampling (non-secret).
    "faithfulness_gate": 0.7,
    "retrieval_disagreement_sample_rate": 0.1,  # 1-in-10 dual-pass checks
    "audit_sample_rate": 0.05,  # fraction of AUTO responses sampled for escalation audit
    # Storage providers (secrets stay in .env).
    "storage_structured_provider": "local",  # local | postgres
    "storage_blob_provider": "local",  # local | s3 | azure | gcs | postgres
    "storage_s3_bucket": "",
    "storage_s3_endpoint_url": "",
    "storage_s3_region": "",
    "storage_azure_container": "app-data",
    "storage_gcs_bucket": "",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


def save_config(updates: dict) -> dict:
    cfg = load_config()
    cfg.update(updates)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    return cfg
