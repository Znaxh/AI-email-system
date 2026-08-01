"""FastAPI backend for the React frontend.

Thin JSON wrapper over the existing `src/` pipeline — nothing company-specific
lives here (every fact is loaded from the active company-data bundle).

Run: .venv/bin/uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import email_source, event_bus, feedback, llm_client, notify, queue_store, router
from src.app_data import (
    PROVIDER_KEY_VARS,
    RESULTS,
    load_user_examples,
    save_user_examples,
    update_env,
)
from src.classifier import CATEGORY_LABELS
from src.company_data import service as company_data
from src.company_data.mapping import canonical_fields_for
from src.company_data.schema import FieldMapping
from src.config import DEFAULTS, load_config, save_config
from src.evaluator import evaluate_generated
from src.generator import generate_reply
from src.schema import (
    GeneratedReply,
    IncomingEmail,
    Remedy,
    Ticket,
    detect_order_id,
    placeholder_transaction,
)
from src.storage.factory import get_structured_store
from src.validate_metric import build_reliability_report

app = FastAPI(title="AI Suggested-Response API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ponytail: dev-open CORS; lock to the deployed origin in prod
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_TABULAR = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".json"}
ALLOWED_POLICY = {".pdf", ".docx", ".md", ".markdown", ".txt", ".text"}

# Cached active company bundle. Cleared when policy / examples / config change.
_CACHE: dict | None = None


def _resources(*, require_ready: bool = True):
    """Return the active company bundle (cached). Legacy data/ import runs once if needed."""
    global _CACHE
    if _CACHE is None:
        cfg = load_config()
        bundle = company_data.load_active_company_bundle(
            allow_empty=True,
            config=cfg,
            include_user_examples=True,
        )
        _CACHE = {
            "bundle": bundle,
            "config": cfg,
            "transactions": bundle.transactions,
            "tickets": bundle.tickets,
            "policy_store": bundle.policy_store,
            "retriever": bundle.retriever,
        }
    bundle = _CACHE["bundle"]
    if require_ready and (bundle.setup_required or not bundle.ready):
        raise HTTPException(
            409,
            "Company data setup required — activate policy + transactions first.",
        )
    return _CACHE


def _set_cache(bundle, cfg: dict | None = None) -> None:
    global _CACHE
    cfg = cfg or load_config()
    _CACHE = {
        "bundle": bundle,
        "config": cfg,
        "transactions": bundle.transactions,
        "tickets": bundle.tickets,
        "policy_store": bundle.policy_store,
        "retriever": bundle.retriever,
    }


def _clear_resources() -> None:
    global _CACHE
    _CACHE = None


def _txn_for(order_id: str | None):
    txns = _resources()["transactions"]
    if order_id and order_id in txns:
        return txns[order_id]
    return placeholder_transaction()


# ============================================================== bootstrap / shared
@app.get("/api/bootstrap")
def bootstrap():
    r = _resources(require_ready=False)
    bundle = r["bundle"]
    cfg = load_config()
    setup = company_data.status()
    return {
        "orders": [
            {"order_id": oid, "product": t.product, "price": t.price, "status": t.status}
            for oid, t in sorted(bundle.transactions.items())
        ],
        "config": {
            "email_source": cfg["email_source"],
            "live_send": cfg["live_send"],
            "t1": cfg["t1"],
            "t2": cfg["t2"],
        },
        "categories": bundle.policy_store.categories() if hasattr(bundle.policy_store, "categories") else [],
        "queue_counts": queue_store.counts(),
        "company_data": setup,
        "setup_required": bool(setup.get("setup_required", True) or bundle.setup_required or not bundle.ready),
        "company_data_version": bundle.version_id,
    }


# ===================================================================== Assistant
class SuggestReq(BaseModel):
    email: str
    order_id: str | None = None
    company_data_version: str | None = None


@app.post("/api/assistant/suggest")
def assistant_suggest(req: SuggestReq):
    if not req.email.strip():
        raise HTTPException(400, "email is required")
    r = _resources()
    bundle = r["bundle"]
    if req.company_data_version and req.company_data_version != bundle.version_id:
        raise HTTPException(409, "Company data changed — reload and try again.")
    txns = r["transactions"]
    detected = detect_order_id(req.email, txns)
    order_id = req.order_id or detected
    txn = _txn_for(order_id)
    gen = generate_reply(req.email, txn, r["policy_store"], r["retriever"])
    return {
        "detected_order_id": detected,
        "order_id": order_id or "",
        "gen": gen.model_dump(),
        "company_data_version": bundle.version_id,
    }


class EvalReq(BaseModel):
    email: str
    order_id: str | None = None
    gen: dict  # a GeneratedReply.model_dump() from /suggest
    company_data_version: str | None = None


@app.post("/api/assistant/evaluate")
def assistant_evaluate(req: EvalReq):
    r = _resources()
    bundle = r["bundle"]
    if req.company_data_version and req.company_data_version != bundle.version_id:
        raise HTTPException(409, "Company data changed — reload and try again.")
    txn = _txn_for(req.order_id)
    gen = GeneratedReply(**req.gen)
    live = Ticket(
        ticket_id="LIVE",
        order_id=txn.order_id,
        category="live",
        split="holdout",
        sentiment="neutral",
        incoming_email=req.email,
        actual_reply=gen.reply,
    )
    ev = evaluate_generated(live, txn, gen, r["policy_store"])
    out = ev.model_dump()
    out["company_data_version"] = bundle.version_id
    return out


# ========================================================================= Inbox
class SyncReq(BaseModel):
    limit: int = 20


@app.post("/api/inbox/sync")
def inbox_sync(req: SyncReq):
    from collections import Counter

    from src.email_parser import parse as parse_email

    r = _resources()
    bundle = r["bundle"]
    cfg = r["config"]
    try:
        emails = email_source.fetch_unread(int(req.limit), cfg)
    except Exception as exc:  # connector/credentials problem — surface, don't crash
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    if not emails:
        return {
            "fetched": 0,
            "tally": {},
            "failed": [],
            "digest": None,
            "company_data_version": bundle.version_id,
        }

    for e in emails:
        event_bus.publish(parse_email(e))

    def _process(email: IncomingEmail) -> dict:
        item = router.route_email(
            email,
            r["transactions"],
            r["policy_store"],
            r["retriever"],
            cfg,
            transaction_missing_fields=bundle.transaction_missing_fields,
            company_data_version=bundle.version_id,
            degraded_bundle=(bundle.quality or {}).get("txn_verdict") == "DEGRADED",
        )
        queue_store.upsert(item)
        return item

    outcomes = event_bus.drain(_process, limit=len(emails))
    items = [o["result"] for o in outcomes if o["ok"]]
    failed = [{"email_id": o["email_id"], "status": o["status"]} for o in outcomes if not o["ok"]]
    tally = Counter(i["decision"] for i in items)
    digest = None
    if cfg.get("digest_enabled") and cfg.get("digest_recipient"):
        digest = notify.send_digest(cfg)
    return {
        "fetched": len(items) + len(failed),
        "tally": {
            "auto": tally.get("auto", 0),
            "review": tally.get("review", 0),
            "escalate": tally.get("escalate", 0),
            "ignore": tally.get("ignore", 0),
        },
        "failed": failed,
        "max_attempts": event_bus.MAX_ATTEMPTS,
        "digest": digest,
        "company_data_version": bundle.version_id,
    }


class RouteOneReq(BaseModel):
    body: str
    subject: str = ""


@app.post("/api/inbox/route-one")
def inbox_route_one(req: RouteOneReq):
    from src.email_parser import parse as parse_email

    if not req.body.strip():
        raise HTTPException(400, "body is required")
    r = _resources()
    bundle = r["bundle"]
    cfg = r["config"]
    email = parse_email(IncomingEmail(
        id=f"manual-{abs(hash(req.body)) % 10**8}",
        subject=req.subject,
        body=req.body,
        from_addr="pasted@manual",
    ))
    try:
        item = router.route_email(
            email,
            r["transactions"],
            r["policy_store"],
            r["retriever"],
            cfg,
            transaction_missing_fields=bundle.transaction_missing_fields,
            company_data_version=bundle.version_id,
            degraded_bundle=(bundle.quality or {}).get("txn_verdict") == "DEGRADED",
        )
        queue_store.upsert(item)
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")
    return item


# ========================================================================= Review
@app.get("/api/queue")
def queue_list(decision: str | None = None, status: str | None = None):
    return queue_store.list_items(decision=decision, status=status)


@app.get("/api/queue/counts")
def queue_counts():
    return queue_store.counts()


def _record(it: dict, label: str, remedy_diff_payload=None) -> None:
    feedback.record_feedback(
        response_id=it.get("response_id") or it["email_id"],
        ticket_id=it["email_id"],
        category=it.get("category") or "",
        cited_rule=(it.get("remedy") or {}).get("rule_cited")
        or (it.get("judge") or {}).get("cited_rule")
        or "",
        routing_decision=it.get("decision") or "",
        ragas_scores=it.get("ragas") or {},
        label=label,
        remedy_diff_payload=remedy_diff_payload,
    )


class SendReq(BaseModel):
    edited: str


@app.post("/api/queue/{email_id}/send")
def queue_send(email_id: str, req: SendReq):
    it = queue_store.get(email_id)
    if not it:
        raise HTTPException(404, "not found")
    cfg = load_config()
    dry = not cfg["live_send"]
    to = IncomingEmail(
        id=it["email_id"], thread_id=it["thread_id"],
        from_addr=it["from_addr"], subject=it["subject"],
    )
    original = it.get("original_reply") or it["suggested_reply"]
    original_remedy = Remedy(**(it.get("remedy") or {}))
    label, diff_payload = feedback.classify_send_label(original, req.edited, original_remedy)
    try:
        res = email_source.send_reply(to, req.edited, dry_run=dry, config=cfg)
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    queue_store.set_status(email_id, "simulated" if dry else "sent", suggested_reply=req.edited)
    _record(it, label, remedy_diff_payload=diff_payload or None)
    return {"detail": res["detail"], "label": label}


@app.post("/api/queue/{email_id}/save")
def queue_save(email_id: str, req: SendReq):
    if not queue_store.get(email_id):
        raise HTTPException(404, "not found")
    queue_store.set_status(email_id, "pending", suggested_reply=req.edited)
    return {"ok": True}


@app.post("/api/queue/{email_id}/dismiss")
def queue_dismiss(email_id: str):
    it = queue_store.get(email_id)
    if not it:
        raise HTTPException(404, "not found")
    _record(it, "ESCALATED_CORRECTLY" if it.get("decision") == "escalate" else "REJECTED")
    queue_store.set_status(email_id, "dismissed")
    return {"ok": True}


@app.post("/api/queue/{email_id}/flag")
def queue_flag(email_id: str):
    it = queue_store.get(email_id)
    if not it:
        raise HTTPException(404, "not found")
    _record(it, "FLAGGED_HALLUCINATION")
    queue_store.set_status(email_id, "dismissed")
    return {"ok": True}


class AuditReq(BaseModel):
    ok: bool  # True = AUTO was fine, False = escalation was missed


@app.post("/api/queue/{email_id}/audit")
def queue_audit(email_id: str, req: AuditReq):
    it = queue_store.get(email_id)
    if not it:
        raise HTTPException(404, "not found")
    if req.ok:
        _record(it, "ACCEPTED_AS_IS")
    else:
        _record(it, "ESCALATED_MISSED")
        queue_store.set_status(email_id, "dismissed")
    return {"ok": True}


@app.post("/api/notify/digest")
def send_digest():
    return notify.send_digest(load_config())


# ===================================================================== Evaluation
@app.get("/api/evaluation/quality")
def evaluation_quality():
    eval_path = RESULTS / "evaluation_results.json"
    ragas_rows = get_structured_store().query("ragas_scores", order_by="-timestamp")
    results = json.loads(eval_path.read_text()) if eval_path.exists() else []
    source = ragas_rows if ragas_rows else results
    # Drop offline mock rows so the Evaluation page never shows stub scores as real.
    def _is_mock(row: dict) -> bool:
        details = row.get("faithfulness_details") or {}
        blob = details if isinstance(details, str) else json.dumps(details)
        return "mock ragas" in blob.lower() or "mock ragas" in str(row.get("note", "")).lower()

    source = [r for r in source if not _is_mock(r)]
    if not source:
        return {"rows": [], "averages": None}

    def avg(key):
        vals = [r[key] for r in source if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None, len(vals)

    faith, nf = avg("faithfulness")
    relev, nr = avg("answer_relevancy")
    prec, np_ = avg("context_precision")
    gated = sum(1 for r in source if r.get("gated_from_auto"))
    return {
        "rows": source,
        "averages": {
            "faithfulness": faith, "n_faithfulness": nf,
            "answer_relevancy": relev, "n_answer_relevancy": nr,
            "context_precision": prec, "n_context_precision": np_,
            "gated": gated, "total": len(source),
        },
    }


@app.get("/api/evaluation/reliability")
def evaluation_reliability():
    return build_reliability_report()


# ======================================================================= Settings
@app.get("/api/settings")
def settings_get():
    cfg = load_config()
    r = _resources(require_ready=False)
    bundle = r["bundle"]
    ps = bundle.policy_store
    gen_provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    cls_provider = (
        os.getenv("CLASSIFY_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "anthropic"
    ).lower()
    setup = company_data.status()
    assets = setup.get("assets") or {}
    pol = assets.get("policy") or {}
    return {
        "config": cfg,
        "policy": {
            "filename": pol.get("filename") or "(none)",
            "rules": len(getattr(ps, "rules", []) or []),
            "categories": ps.categories() if hasattr(ps, "categories") else [],
            "preview": (ps.chunks[0] if getattr(ps, "chunks", None) else ""),
        },
        "company_data": setup,
        "canonical_fields": {
            "transactions": canonical_fields_for("transactions"),
            "tickets": canonical_fields_for("tickets"),
        },
        "providers": [
            {
                "name": p,
                "configured": bool(os.getenv(key_var)),
                "used_for": [
                    u
                    for u, prov in (("generation", gen_provider), ("categorization", cls_provider))
                    if prov == p
                ],
            }
            for p, key_var in PROVIDER_KEY_VARS.items()
        ],
        "llm": {
            "gen_provider": gen_provider,
            "gen_model": os.getenv("LLM_MODEL", ""),
            "cls_provider": cls_provider,
            "cls_model": os.getenv("CLASSIFY_LLM_MODEL", ""),
            "default_models": llm_client.DEFAULT_MODELS,
        },
        "category_labels": CATEGORY_LABELS,
        "examples": load_user_examples(),
        "defaults": DEFAULTS,
    }


class ConfigReq(BaseModel):
    updates: dict


@app.post("/api/settings/config")
def settings_config(req: ConfigReq):
    if "t1" in req.updates and "t2" in req.updates and req.updates["t2"] >= req.updates["t1"]:
        raise HTTPException(400, "T2 must be below T1.")
    save_config(req.updates)
    _clear_resources()
    return {"ok": True, "config": load_config()}


class EnvReq(BaseModel):
    updates: dict  # value None removes the key; keys are env var names


@app.post("/api/settings/env")
def settings_env(req: EnvReq):
    update_env(req.updates)
    return {"ok": True}


class LLMSaveReq(BaseModel):
    step: str  # "generate" | "classify"
    provider: str
    model: str = ""
    api_key: str = ""


@app.post("/api/settings/llm")
def settings_llm(req: LLMSaveReq):
    if req.provider not in PROVIDER_KEY_VARS:
        raise HTTPException(400, f"unknown provider {req.provider}")
    prefix = "" if req.step == "generate" else "CLASSIFY_"
    updates: dict[str, str | None] = {
        f"{prefix}LLM_PROVIDER": req.provider,
        f"{prefix}LLM_MODEL": req.model.strip() or None,
    }
    if req.api_key.strip():
        updates[PROVIDER_KEY_VARS[req.provider]] = req.api_key.strip()
    update_env(updates)
    return {"ok": True}


@app.post("/api/settings/test/{target}")
def settings_test(target: str):
    try:
        if target in ("generate", "classify"):
            out = llm_client.complete(
                "You are a connectivity check. Reply with the single word OK.",
                "ping", max_tokens=8, purpose=target,
            )
            p, m = llm_client.resolve_provider_model(target)
            return {"ok": True, "detail": f"{p} ({m}) replied: {out.strip()[:40]}"}
        if target == "inbox":
            got = email_source.fetch_unread(1, load_config())
            return {"ok": True, "detail": f"fetched {len(got)} message(s)"}
        if target == "storage":
            from src.storage.factory import get_blob_store
            get_structured_store().test_connection()
            get_blob_store().test_connection()
            return {"ok": True, "detail": "structured + blob connected"}
        raise HTTPException(404, f"unknown test target {target}")
    except HTTPException:
        raise
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


class ExamplesReq(BaseModel):
    examples: list[dict]


@app.post("/api/settings/examples")
def settings_examples(req: ExamplesReq):
    for i, row in enumerate(req.examples):
        if not row.get("ticket_id"):
            row["ticket_id"] = f"U{i + 1:03d}"
    save_user_examples(req.examples)
    _clear_resources()
    return {"ok": True, "examples": load_user_examples()}


@app.get("/api/settings/company-data")
def company_data_status():
    return company_data.status()


@app.post("/api/settings/company-data/stage")
async def company_data_stage(file: UploadFile, target: str):
    if target not in ("policy", "transactions", "tickets"):
        raise HTTPException(400, f"unknown target {target}")
    suffix = Path(file.filename or "").suffix.lower()
    allowed = ALLOWED_POLICY if target == "policy" else ALLOWED_TABULAR
    if suffix not in allowed:
        raise HTTPException(400, f"unsupported extension {suffix}; allowed: {sorted(allowed)}")
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"file too large (max {MAX_UPLOAD_BYTES} bytes)")
    if not raw:
        raise HTTPException(400, "empty file")
    try:
        meta = company_data.stage_upload(
            raw, filename=file.filename or f"{target}{suffix}", target=target
        )
    except Exception as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")
    return {"ok": True, **{k: v for k, v in meta.items() if k != "local_path"}}


@app.get("/api/settings/company-data/preview/{token}")
def company_data_preview(token: str, sheet: str | None = None):
    try:
        prev = company_data.preview_staged(token, sheet=sheet)
    except FileNotFoundError:
        raise HTTPException(404, "staging token not found")
    except Exception as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")
    return prev.to_dict()


class DryRunReq(BaseModel):
    mapping: dict = {}
    file_hash: str | None = None


@app.post("/api/settings/company-data/dry-run/{token}")
def company_data_dry_run(token: str, req: DryRunReq):
    try:
        meta = company_data._staging_meta(token)  # noqa: SLF001
        if req.file_hash and req.file_hash != meta.get("file_hash"):
            raise HTTPException(400, "file_hash mismatch — restage the upload")
        result = company_data.dry_run_staged(token, FieldMapping.from_dict(req.mapping))
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(404, "staging token not found")
    except Exception as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")
    return result.to_dict()


class ActivateAsset(BaseModel):
    token: str
    mapping: dict = {}
    file_hash: str | None = None


class ActivateReq(BaseModel):
    policy: ActivateAsset
    transactions: ActivateAsset
    tickets: ActivateAsset | None = None
    confirm_degraded: bool = False


@app.post("/api/settings/company-data/activate")
def company_data_activate(req: ActivateReq):
    assets = {
        "policy": {
            "token": req.policy.token,
            "mapping": req.policy.mapping,
            "file_hash": req.policy.file_hash,
        },
        "transactions": {
            "token": req.transactions.token,
            "mapping": req.transactions.mapping,
            "file_hash": req.transactions.file_hash,
        },
    }
    if req.tickets:
        assets["tickets"] = {
            "token": req.tickets.token,
            "mapping": req.tickets.mapping,
            "file_hash": req.tickets.file_hash,
        }
    try:
        bundle = company_data.activate_staged(
            assets,
            confirm_degraded=req.confirm_degraded,
            config=load_config(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")
    _set_cache(bundle)
    return {
        "ok": True,
        "version_id": bundle.version_id,
        "quality": bundle.quality,
        "status": company_data.status(),
    }


class RollbackReq(BaseModel):
    version_id: str


@app.post("/api/settings/company-data/rollback")
def company_data_rollback(req: RollbackReq):
    try:
        bundle = company_data.rollback(req.version_id, config=load_config())
    except FileNotFoundError:
        raise HTTPException(404, f"version not found: {req.version_id}")
    except Exception as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")
    _set_cache(bundle)
    return {"ok": True, "version_id": bundle.version_id, "status": company_data.status()}


@app.post("/api/settings/policy")
async def settings_policy(file: UploadFile):
    """Replace policy while keeping the active transactions/tickets version."""
    setup = company_data.status()
    if setup.get("setup_required"):
        raise HTTPException(
            409,
            "No active company data yet — use Company Data setup to upload policy and transactions together.",
        )
    suffix = Path(file.filename or "policy.pdf").suffix.lower() or ".pdf"
    if suffix not in ALLOWED_POLICY:
        raise HTTPException(400, f"unsupported policy extension {suffix}")
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "file too large")
    try:
        from src.company_data.service import VERSIONS_PREFIX, _blob

        pol = company_data.stage_upload(raw, filename=f"policy{suffix}", target="policy")
        active = setup["active_version"]
        blob = _blob()
        manifest = json.loads(blob.get(f"{VERSIONS_PREFIX}/{active}/manifest.json").decode())
        txn_info = (manifest.get("assets") or {}).get("transactions") or {}
        txn_key = f"{VERSIONS_PREFIX}/{active}/source/transactions/{txn_info.get('filename')}"
        txn_source = blob.get(txn_key)
        txn_stage = company_data.stage_upload(
            txn_source,
            filename=txn_info.get("filename") or "transactions.json",
            target="transactions",
        )
        assets = {
            "policy": {"token": pol["token"], "mapping": {}, "file_hash": pol["file_hash"]},
            "transactions": {
                "token": txn_stage["token"],
                "mapping": txn_info.get("mapping") or {},
                "file_hash": txn_stage["file_hash"],
            },
        }
        tickets_info = (manifest.get("assets") or {}).get("tickets")
        if tickets_info and tickets_info.get("count"):
            tkeys = [
                k
                for k in blob.list(f"{VERSIONS_PREFIX}/{active}/source/tickets/")
                if not k.endswith("/")
            ]
            if tkeys:
                t_raw = blob.get(tkeys[0])
                t_stage = company_data.stage_upload(
                    t_raw, filename=Path(tkeys[0]).name, target="tickets"
                )
                assets["tickets"] = {
                    "token": t_stage["token"],
                    "mapping": tickets_info.get("mapping") or {},
                    "file_hash": t_stage["file_hash"],
                }
        bundle = company_data.activate_staged(
            assets,
            confirm_degraded=True,
            config=load_config(),
        )
    except Exception as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")
    _set_cache(bundle)
    return {
        "ok": True,
        "filename": f"policy{suffix}",
        "rules": len(bundle.policy_store.rules),
        "version_id": bundle.version_id,
    }


# --------------------------------------------------------- serve built frontend
# Assets from the mount; every other non-/api path falls back to index.html so
# client-side routes (/review, /settings, …) survive a hard refresh.
_DIST = Path("web/dist")
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        return FileResponse(_DIST / "index.html")
