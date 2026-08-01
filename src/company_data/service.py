"""Public company-data API: preview, dry-run, normalize, activate, rollback, load."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.company_data.mapping import suggest_mapping
from src.company_data.profile import profile_table
from src.company_data.readers import list_sheets, read_table
from src.company_data.schema import (
    MIN_USEFUL_TONE_CORPUS,
    DryRunResult,
    FieldMapping,
    PreviewResult,
    Target,
)
from src.company_data.validate import dry_run_upload, file_hash, normalize
from src.schema import Ticket, Transaction

_LOCK = threading.RLock()
_POLICY_CACHE_DIR = Path("results/company_cache/policy")
ACTIVE_KEY = "company/active.json"
STAGING_PREFIX = "company/staging"
VERSIONS_PREFIX = "company/versions"


@dataclass(frozen=True)
class CompanyBundle:
    version_id: str
    transactions: dict[str, Transaction]
    tickets: list[Ticket]
    policy_path: str
    policy_store: Any  # PolicyStore — avoided circular import at type time
    retriever: Any  # TicketRetriever
    transaction_missing_fields: dict[str, frozenset[str]] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    ready: bool = True
    setup_required: bool = False


_EMPTY_QUALITY = {
    "tone_corpus_size": 0,
    "weak_tone_corpus": True,
    "advisories": ["setup_required"],
    "capability_impact": [],
}


def preview(path: str | Path, target: Target, sheet: str | None = None) -> PreviewResult:
    path = Path(path)
    if target == "policy":
        return PreviewResult(
            columns=[],
            samples=[],
            suggested_mapping=FieldMapping(),
            file_hash=file_hash(path),
            target="policy",
            row_count=0,
        )
    sheets = list_sheets(path) if path.suffix.lower() in (".xlsx", ".xlsm", ".xls") else []
    table = read_table(path, sheet=sheet or (sheets[0] if sheets else None))
    prof = profile_table(table)
    suggested = suggest_mapping(table, target)
    return PreviewResult(
        columns=prof["columns"],
        samples=prof["samples"],
        suggested_mapping=suggested,
        sheets=sheets or table.sheets,
        fill_rates=prof["fill_rates"],
        date_candidates=prof["date_candidates"],
        money_candidates=prof["money_candidates"],
        file_hash=file_hash(path),
        target=target,
        row_count=prof["row_count"],
    )


def stage_upload(
    raw: bytes,
    *,
    filename: str,
    target: Target,
) -> dict[str, Any]:
    """Persist raw bytes under company/staging/{token}/ and return staging metadata."""
    token = uuid.uuid4().hex
    safe_name = Path(filename or f"upload-{target}").name.replace("..", "")
    key = f"{STAGING_PREFIX}/{token}/{safe_name}"
    blob = _blob()
    blob.put(key, raw)
    meta = {
        "token": token,
        "target": target,
        "filename": safe_name,
        "key": key,
        "file_hash": hashlib.sha256(raw).hexdigest(),
        "staged_at": _now(),
    }
    blob.put(f"{STAGING_PREFIX}/{token}/meta.json", json.dumps(meta), "application/json")
    # Local materialization for parsers that need a filesystem path.
    local = _materialize(key, raw, subdir=f"staging/{token}")
    meta["local_path"] = str(local)
    return meta


def preview_staged(token: str, *, sheet: str | None = None) -> PreviewResult:
    meta = _staging_meta(token)
    return preview(meta["local_path"], meta["target"], sheet=sheet)


def dry_run_staged(
    token: str,
    mapping: FieldMapping | dict | None = None,
    *,
    known_order_ids: set[str] | None = None,
) -> DryRunResult:
    meta = _staging_meta(token)
    mapping = FieldMapping.from_dict(mapping) if isinstance(mapping, dict) else (mapping or FieldMapping())
    if meta["target"] != "policy" and known_order_ids is None and meta["target"] == "tickets":
        bundle = load_active_company_bundle(allow_empty=True)
        known_order_ids = set(bundle.transactions) if bundle and not bundle.setup_required else set()
    return dry_run_upload(meta["local_path"], meta["target"], mapping, known_order_ids=known_order_ids)


def activate_staged(
    assets: dict[str, dict[str, Any]],
    *,
    confirm_degraded: bool = False,
    config: dict | None = None,
) -> CompanyBundle:
    """Activate a new company version from staged assets.

    assets = {
      "policy": {"token": ..., "mapping": {}},
      "transactions": {"token": ..., "mapping": {...}, "file_hash": ...},
      "tickets": {"token": ..., "mapping": {...}} | None,
    }
    """
    with _LOCK:
        return _activate_locked(assets, confirm_degraded=confirm_degraded, config=config)


def rollback(version_id: str, *, config: dict | None = None) -> CompanyBundle:
    with _LOCK:
        bundle = _build_bundle_from_version(version_id, config=config)
        _write_active({"version_id": version_id, "activated_at": _now(), "via": "rollback"})
        return bundle


def status() -> dict[str, Any]:
    active = _read_active()
    if not active:
        # Attempt one-time legacy import.
        imported = maybe_import_legacy_data()
        active = _read_active()
        if not active:
            return {
                "ready": False,
                "setup_required": True,
                "active_version": None,
                "assets": {},
                "advisories": ["Upload a policy document and a transactions file to begin."],
                "imported_legacy": imported,
            }
    version_id = active["version_id"]
    manifest = _read_json(f"{VERSIONS_PREFIX}/{version_id}/manifest.json") or {}
    return {
        "ready": True,
        "setup_required": False,
        "active_version": version_id,
        "activated_at": active.get("activated_at"),
        "assets": manifest.get("assets") or {},
        "quality": manifest.get("quality") or {},
        "advisories": (manifest.get("quality") or {}).get("advisories") or [],
        "capability_impact": (manifest.get("quality") or {}).get("capability_impact") or [],
        "versions": _list_versions(),
    }


def load_active_company_bundle(
    *,
    allow_empty: bool = False,
    config: dict | None = None,
    include_user_examples: bool = True,
) -> CompanyBundle:
    active = _read_active()
    if not active:
        maybe_import_legacy_data()
        active = _read_active()
    if not active:
        if allow_empty:
            return _empty_bundle()
        raise FileNotFoundError("no active company data — complete setup in Settings")
    return _build_bundle_from_version(
        active["version_id"],
        config=config,
        include_user_examples=include_user_examples,
    )


def maybe_import_legacy_data() -> bool:
    """One-time import from data/ when no active manifest exists."""
    with _LOCK:
        if _read_active():
            return False
        data = Path("data")
        txn_path = data / "transactions.json"
        ticket_path = data / "dataset.json"
        # Find any policy.*
        policy_path = None
        for cand in sorted(data.glob("policy.*")):
            policy_path = cand
            break
        if not (txn_path.exists() and policy_path and policy_path.exists()):
            return False

        from src.company_data.schema import FieldMapping

        # Stage copies
        assets: dict[str, dict] = {}
        for target, path in (("policy", policy_path), ("transactions", txn_path)):
            raw = path.read_bytes()
            meta = stage_upload(raw, filename=path.name, target=target)  # type: ignore[arg-type]
            mapping = FieldMapping()
            if target == "transactions":
                prev = preview(meta["local_path"], "transactions")
                mapping = prev.suggested_mapping
            assets[target] = {
                "token": meta["token"],
                "mapping": mapping.to_dict(),
                "file_hash": meta["file_hash"],
            }
        if ticket_path.exists():
            raw = ticket_path.read_bytes()
            meta = stage_upload(raw, filename=ticket_path.name, target="tickets")
            prev = preview(meta["local_path"], "tickets")
            assets["tickets"] = {
                "token": meta["token"],
                "mapping": prev.suggested_mapping.to_dict(),
                "file_hash": meta["file_hash"],
            }
        try:
            _activate_locked(assets, confirm_degraded=True, config=None, via="legacy_import")
            return True
        except Exception:
            return False


# --------------------------------------------------------------------------- internals


def _activate_locked(
    assets: dict[str, dict[str, Any]],
    *,
    confirm_degraded: bool,
    config: dict | None,
    via: str = "activate",
) -> CompanyBundle:
    if "policy" not in assets or "transactions" not in assets:
        raise ValueError("policy and transactions are required to activate")

    version_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    version_prefix = f"{VERSIONS_PREFIX}/{version_id}"
    blob = _blob()

    # --- policy
    pol_meta = _staging_meta(assets["policy"]["token"])
    pol_dry = dry_run_upload(pol_meta["local_path"], "policy", FieldMapping())
    if pol_dry.verdict == "BLOCKED":
        raise ValueError(f"policy blocked: {[i.message for i in pol_dry.issues]}")
    pol_raw = Path(pol_meta["local_path"]).read_bytes()
    pol_name = pol_meta["filename"]
    pol_hash = hashlib.sha256(pol_raw).hexdigest()
    blob.put(f"{version_prefix}/source/policy/{pol_name}", pol_raw)
    policy_local = _materialize(
        f"{version_prefix}/source/policy/{pol_name}",
        pol_raw,
        subdir=f"versions/{version_id}/policy",
    )

    # --- transactions
    txn_meta = _staging_meta(assets["transactions"]["token"])
    expected_hash = assets["transactions"].get("file_hash")
    if expected_hash and expected_hash != txn_meta["file_hash"]:
        raise ValueError("transactions file_hash mismatch — restage the upload")
    txn_mapping = FieldMapping.from_dict(assets["transactions"].get("mapping") or {})
    txn_dry = dry_run_upload(txn_meta["local_path"], "transactions", txn_mapping)
    if txn_dry.verdict == "BLOCKED":
        raise ValueError(f"transactions blocked: {[i.message for i in txn_dry.issues]}")
    if txn_dry.verdict == "DEGRADED" and not confirm_degraded:
        raise ValueError("DEGRADED transactions require confirm_degraded=True")
    txn_records, txn_manifest = normalize(
        txn_meta["local_path"],
        "transactions",
        txn_mapping,
        allow_degraded=confirm_degraded or txn_dry.verdict == "READY",
    )
    blob.put(
        f"{version_prefix}/source/transactions/{txn_meta['filename']}",
        Path(txn_meta["local_path"]).read_bytes(),
    )
    blob.put(
        f"{version_prefix}/normalized/transactions.json",
        json.dumps(txn_records, indent=2, default=str),
        "application/json",
    )

    # --- tickets (optional)
    ticket_records: list[dict] = []
    ticket_manifest: dict = {}
    ticket_dry: DryRunResult | None = None
    if assets.get("tickets"):
        t_meta = _staging_meta(assets["tickets"]["token"])
        t_mapping = FieldMapping.from_dict(assets["tickets"].get("mapping") or {})
        known = {r["order_id"] for r in txn_records}
        ticket_dry = dry_run_upload(t_meta["local_path"], "tickets", t_mapping, known_order_ids=known)
        if ticket_dry.verdict == "BLOCKED":
            raise ValueError(f"tickets blocked: {[i.message for i in ticket_dry.issues]}")
        ticket_records, ticket_manifest = normalize(
            t_meta["local_path"],
            "tickets",
            t_mapping,
            known_order_ids=known,
            allow_degraded=True,
        )
        blob.put(
            f"{version_prefix}/source/tickets/{t_meta['filename']}",
            Path(t_meta["local_path"]).read_bytes(),
        )
        blob.put(
            f"{version_prefix}/normalized/tickets.json",
            json.dumps(ticket_records, indent=2, default=str),
            "application/json",
        )

    # Build + verify policy index under namespaced key before flipping active.
    from src.policy_store import PolicyStore

    cfg = config or {}
    policy_store = PolicyStore(
        str(policy_local),
        config={**cfg, "policy_index_namespace": pol_hash},
    )
    if not policy_store.rules:
        raise ValueError("policy index produced zero rules")
    # Smoke retrieval
    _ = policy_store.retrieve("refund return order", k=1)

    corpus_n = sum(1 for t in ticket_records if t.get("split") == "corpus")
    quality = {
        "tone_corpus_size": corpus_n,
        "weak_tone_corpus": corpus_n < MIN_USEFUL_TONE_CORPUS,
        "advisories": list(ticket_dry.advisories) if ticket_dry else (
            ["no corpus tickets — generation will use policy + transactions only"] if not ticket_records else []
        ),
        "capability_impact": list(txn_dry.capability_impact),
        "txn_verdict": txn_dry.verdict,
        "policy_hash": pol_hash,
    }
    if quality["weak_tone_corpus"] and corpus_n > 0:
        if "weak:tone_corpus" not in quality["advisories"]:
            quality["advisories"].append("weak:tone_corpus")

    manifest = {
        "version_id": version_id,
        "created_at": _now(),
        "via": via,
        "assets": {
            "policy": {"filename": pol_name, "file_hash": pol_hash, "rules": len(policy_store.rules)},
            "transactions": {
                "filename": txn_meta["filename"],
                "file_hash": txn_meta["file_hash"],
                "count": len(txn_records),
                "verdict": txn_dry.verdict,
                "mapping": txn_mapping.to_dict(),
            },
            "tickets": {
                "count": len(ticket_records),
                "corpus": corpus_n,
                "holdout": sum(1 for t in ticket_records if t.get("split") == "holdout"),
                "mapping": (assets.get("tickets") or {}).get("mapping"),
            }
            if ticket_records or assets.get("tickets")
            else None,
        },
        "quality": quality,
        "txn_manifest": txn_manifest,
        "ticket_manifest": ticket_manifest,
        "policy_path_key": f"{version_prefix}/source/policy/{pol_name}",
    }
    blob.put(f"{version_prefix}/manifest.json", json.dumps(manifest, indent=2), "application/json")

    # Build full bundle BEFORE flipping active pointer.
    bundle = _assemble_bundle(
        version_id=version_id,
        txn_records=txn_records,
        ticket_records=ticket_records,
        policy_path=str(policy_local),
        policy_store=policy_store,
        quality=quality,
        config=cfg,
        include_user_examples=True,
    )
    _write_active({"version_id": version_id, "activated_at": _now(), "via": via})
    return bundle


def _build_bundle_from_version(
    version_id: str,
    *,
    config: dict | None = None,
    include_user_examples: bool = True,
) -> CompanyBundle:
    manifest = _read_json(f"{VERSIONS_PREFIX}/{version_id}/manifest.json")
    if not manifest:
        raise FileNotFoundError(f"company version not found: {version_id}")
    txn_records = _read_json(f"{VERSIONS_PREFIX}/{version_id}/normalized/transactions.json") or []
    ticket_records = _read_json(f"{VERSIONS_PREFIX}/{version_id}/normalized/tickets.json") or []

    pol_info = (manifest.get("assets") or {}).get("policy") or {}
    pol_hash = (manifest.get("quality") or {}).get("policy_hash") or pol_info.get("file_hash")
    pol_key = manifest.get("policy_path_key") or ""
    pol_raw = _blob().get(pol_key)
    policy_local = _materialize(pol_key, pol_raw, subdir=f"versions/{version_id}/policy")

    from src.policy_store import PolicyStore

    cfg = config or {}
    policy_store = PolicyStore(
        str(policy_local),
        config={**cfg, "policy_index_namespace": pol_hash},
    )
    if not policy_store.rules:
        raise ValueError(f"policy index invalid for version {version_id}")
    _ = policy_store.retrieve("refund return order", k=1)

    return _assemble_bundle(
        version_id=version_id,
        txn_records=txn_records,
        ticket_records=ticket_records,
        policy_path=str(policy_local),
        policy_store=policy_store,
        quality=manifest.get("quality") or {},
        config=cfg,
        include_user_examples=include_user_examples,
    )


def _assemble_bundle(
    *,
    version_id: str,
    txn_records: list[dict],
    ticket_records: list[dict],
    policy_path: str,
    policy_store: Any,
    quality: dict,
    config: dict,
    include_user_examples: bool,
) -> CompanyBundle:
    from src.app_data import user_examples_as_tickets
    from src.retriever import TicketRetriever

    missing: dict[str, frozenset[str]] = {}
    transactions: dict[str, Transaction] = {}
    for row in txn_records:
        data = dict(row)
        miss = data.pop("_missing_fields", []) or []
        # extras may remain as extra="allow"
        extras = data.pop("extras", None)
        if isinstance(extras, dict):
            for k, v in extras.items():
                data.setdefault(k, v)
        # Safe defaults for relaxed fields
        data.setdefault("customer_id", "")
        data.setdefault("product", "(unspecified product)")
        data.setdefault("price", 0.0)
        data.setdefault("order_date", "")
        data.setdefault("status", "")
        data.setdefault("final_sale", False)
        data.setdefault("returns_last_90_days", 0)
        txn = Transaction(**{k: v for k, v in data.items() if k != "_missing_fields"})
        transactions[txn.order_id] = txn
        missing[txn.order_id] = frozenset(miss)

    tickets: list[Ticket] = []
    for row in ticket_records:
        data = dict(row)
        data.pop("extras", None)
        data.setdefault("order_id", "")
        data.setdefault("category", "uncategorized")
        data.setdefault("split", "corpus")
        data.setdefault("sentiment", "neutral")
        # Holdout with cleared order link: keep for eval exclusion; still build Ticket
        tickets.append(Ticket(**data))

    corpus_tickets = list(tickets)
    if include_user_examples:
        corpus_tickets = tickets + user_examples_as_tickets()

    weak = bool(quality.get("weak_tone_corpus", True))
    retriever = TicketRetriever(corpus_tickets, disable_neighbors=weak)

    return CompanyBundle(
        version_id=version_id,
        transactions=transactions,
        tickets=tickets,
        policy_path=policy_path,
        policy_store=policy_store,
        retriever=retriever,
        transaction_missing_fields=missing,
        quality=quality,
        ready=True,
        setup_required=False,
    )


def _empty_bundle() -> CompanyBundle:
    class _Empty:
        rules = []
        chunks = []

        def categories(self):
            return []

        def retrieve(self, *a, **k):
            return []

        def retrieve_rules(self, *a, **k):
            return []

        def all_text(self):
            return ""

        def rule_by_id(self, _):
            return None

        def top_k(self, *a, **k):
            return []

    from src.retriever import TicketRetriever

    empty = _Empty()
    return CompanyBundle(
        version_id="",
        transactions={},
        tickets=[],
        policy_path="",
        policy_store=empty,
        retriever=TicketRetriever([], disable_neighbors=True),
        transaction_missing_fields={},
        quality=dict(_EMPTY_QUALITY),
        ready=False,
        setup_required=True,
    )


def _blob():
    from src.storage.factory import get_blob_store

    return get_blob_store()


def _materialize(key: str, raw: bytes, *, subdir: str) -> Path:
    dest_dir = _POLICY_CACHE_DIR.parent / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(key).name or "blob"
    dest = dest_dir / name
    dest.write_bytes(raw)
    return dest


def _staging_meta(token: str) -> dict[str, Any]:
    meta = _read_json(f"{STAGING_PREFIX}/{token}/meta.json")
    if not meta:
        raise FileNotFoundError(f"staging token not found: {token}")
    # Ensure local path exists (blob meta never stores local_path).
    key = meta["key"]
    local_s = meta.get("local_path") or ""
    local = Path(local_s) if local_s else None
    if local is None or not local.exists() or not local.is_file():
        raw = _blob().get(key)
        local = _materialize(key, raw, subdir=f"staging/{token}")
        meta["local_path"] = str(local)
    return meta


def _read_active() -> dict | None:
    return _read_json(ACTIVE_KEY)


def _write_active(obj: dict) -> None:
    _blob().put(ACTIVE_KEY, json.dumps(obj, indent=2), "application/json")


def _read_json(key: str) -> Any:
    try:
        return json.loads(_blob().get(key).decode("utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _list_versions() -> list[str]:
    try:
        keys = _blob().list(VERSIONS_PREFIX)
    except Exception:
        return []
    versions = set()
    for k in keys:
        parts = k.split("/")
        if len(parts) >= 3 and parts[0] == "company" and parts[1] == "versions":
            versions.add(parts[2])
    return sorted(versions, reverse=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
