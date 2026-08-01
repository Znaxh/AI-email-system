"""Dry-run validation — verdict, per-row issues, fill rates, capability impact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.company_data.coerce import apply_ticket_coercion, apply_txn_coercion
from src.company_data.mapping import mapped_canonical_set, project_rows, validate_mapping
from src.company_data.profile import profile_table, resolve_date_order, resolve_money_style
from src.company_data.readers import StringTable, read_table
from src.company_data.schema import (
    MIN_USEFUL_TONE_CORPUS,
    RECOMMENDED_TXN_FIELDS,
    DryRunResult,
    FieldMapping,
    Issue,
    Target,
    Verdict,
)


def dry_run_upload(
    path: str | Path,
    target: Target,
    mapping: FieldMapping,
    *,
    known_order_ids: set[str] | None = None,
) -> DryRunResult:
    if target == "policy":
        return _dry_run_policy(path)

    table = read_table(path, sheet=mapping.sheet)
    issues: list[Issue] = []
    for w in table.warnings:
        if w.startswith("precision_risk_id"):
            issues.append(Issue("error", "precision_risk_id", w))

    map_errors = validate_mapping(mapping, target, table.headers)
    for msg in map_errors:
        issues.append(Issue("error", "mapping_invalid", msg))

    # Resolve date/money conventions once per column — block if ambiguous.
    if target == "transactions" and not map_errors:
        issues.extend(_resolve_conventions(table, mapping))

    if any(i.level == "error" and i.code in {"mapping_invalid", "ambiguous_date", "ambiguous_money", "precision_risk_id"} for i in issues):
        return DryRunResult(
            verdict="BLOCKED",
            issues=issues,
            fill_rates=profile_table(table)["fill_rates"],
            counts={"rows": table.row_count},
        )

    if target == "transactions":
        return _dry_run_transactions(table, mapping, issues)
    return _dry_run_tickets(table, mapping, issues, known_order_ids=known_order_ids or set())


def normalize(
    path: str | Path,
    target: Target,
    mapping: FieldMapping,
    *,
    known_order_ids: set[str] | None = None,
    allow_degraded: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize records after a passing dry run. Raises ValueError if BLOCKED."""
    result = dry_run_upload(path, target, mapping, known_order_ids=known_order_ids)
    if result.verdict == "BLOCKED":
        raise ValueError(f"cannot normalize BLOCKED upload: {[i.message for i in result.issues[:5]]}")
    if result.verdict == "DEGRADED" and not allow_degraded:
        raise ValueError("DEGRADED upload requires explicit confirmation (allow_degraded=True)")

    if target == "policy":
        raw = Path(path).read_bytes()
        manifest = {
            "target": "policy",
            "file_hash": hashlib.sha256(raw).hexdigest(),
            "filename": Path(path).name,
            "verdict": result.verdict,
            "mapping": mapping.to_dict(),
            "counts": result.counts,
        }
        return [], manifest

    table = read_table(path, sheet=mapping.sheet)
    if target == "transactions":
        records, _missing_map = _normalize_transactions(table, mapping)
    else:
        records = _normalize_tickets(table, mapping, known_order_ids=known_order_ids or set())
    manifest = {
        "target": target,
        "file_hash": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "filename": Path(path).name,
        "verdict": result.verdict,
        "mapping": mapping.to_dict(),
        "counts": result.counts,
        "capability_impact": result.capability_impact,
        "advisories": result.advisories,
        "issues": [
            {"level": i.level, "code": i.code, "message": i.message, "row": i.row, "field": i.field}
            for i in result.issues
            if i.level != "info"
        ],
    }
    return records, manifest


def _resolve_conventions(table: StringTable, mapping: FieldMapping) -> list[Issue]:
    issues: list[Issue] = []
    headers = table.headers
    for canon, src in mapping.fields.items():
        if not src or src not in headers:
            continue
        col_i = headers.index(src)
        values = [row[col_i] for row in table.rows if col_i < len(row)]
        if canon in ("order_date", "delivery_date", "promised_delivery_date"):
            forced = mapping.date_orders.get(canon)
            order = resolve_date_order(values, forced=forced)
            if order is None and any(str(v).strip() for v in values):
                issues.append(
                    Issue(
                        "error",
                        "ambiguous_date",
                        f"date order for {canon!r} (column {src!r}) is ambiguous — choose DMY, MDY, or YMD",
                        field=canon,
                    )
                )
            elif order:
                mapping.date_orders[canon] = order
        if canon == "price":
            forced = mapping.money_styles.get(canon)
            style = resolve_money_style(values, forced=forced)
            if style is None and any(str(v).strip() for v in values):
                issues.append(
                    Issue(
                        "error",
                        "ambiguous_money",
                        f"money format for {canon!r} (column {src!r}) is ambiguous — choose decimal style",
                        field=canon,
                    )
                )
            elif style:
                mapping.money_styles[canon] = style
    return issues


def _dry_run_transactions(table: StringTable, mapping: FieldMapping, issues: list[Issue]) -> DryRunResult:
    records, missing_map, row_issues, blocked = _normalize_transactions(table, mapping, collect_issues=True)
    issues.extend(row_issues)
    fill = {f: 0.0 for f in mapping.fields}
    n = max(len(records), 1)
    for f in mapping.fields:
        present = sum(1 for r in records if f not in r.get("_missing_fields", []) and r.get(f) not in (None, ""))
        # For price, presence means not in missing_fields
        if f == "price":
            present = sum(1 for r in records if "price" not in r.get("_missing_fields", []))
        fill[f] = present / n

    capability: list[str] = []
    unmapped_recommended = [f for f in RECOMMENDED_TXN_FIELDS if not mapping.source_for(f)]
    for f in unmapped_recommended:
        capability.append(f"missing recommended field {f}")

    # Also degraded if many rows miss a recommended field even when mapped
    for f in RECOMMENDED_TXN_FIELDS:
        miss_rate = sum(1 for r in records if f in r.get("_missing_fields", [])) / n
        if miss_rate > 0.5 and mapping.source_for(f):
            capability.append(f"majority of rows missing {f} ({miss_rate:.0%})")

    verdict: Verdict = "BLOCKED" if blocked or not records else ("DEGRADED" if capability else "READY")
    if not mapping.source_for("order_id"):
        verdict = "BLOCKED"
        issues.append(Issue("error", "required_unmapped", "order_id is required", field="order_id"))

    samples = []
    for r in records[:3]:
        samples.append({k: v for k, v in r.items() if not k.startswith("_") or k == "_missing_fields"})

    return DryRunResult(
        verdict=verdict,
        issues=issues,
        fill_rates=fill,
        counts={"rows": len(records), "unique_order_ids": len({r["order_id"] for r in records})},
        capability_impact=capability,
        sample_rows=samples,
    )


def _normalize_transactions(
    table: StringTable,
    mapping: FieldMapping,
    *,
    collect_issues: bool = False,
) -> Any:
    mapped = mapped_canonical_set(mapping)
    projected = project_rows(table, mapping)
    by_id: dict[str, dict[str, Any]] = {}
    issues: list[Issue] = []
    blocked = False
    missing_map: dict[str, list[str]] = {}

    for i, raw in enumerate(projected, start=2):  # 1-indexed header → data starts at 2
        extras = raw.pop("_extras", {}) if isinstance(raw.get("_extras"), dict) else {}
        # Flatten projected to str dict
        str_raw = {k: ("" if v is None else str(v)) for k, v in raw.items() if k != "_extras"}
        values, missing, failures = apply_txn_coercion(
            i,
            str_raw,
            date_orders=mapping.date_orders,
            money_styles=mapping.money_styles,
            mapped_fields=mapped,
        )
        for f in failures:
            issues.append(f.as_issue())
            if f.field in ("order_id",) or f.message.startswith("date order"):
                blocked = True

        oid = values.get("order_id") or ""
        if not oid:
            issues.append(Issue("error", "missing_order_id", "order_id is empty", row=i, field="order_id"))
            blocked = True
            continue

        values["_missing_fields"] = sorted(missing)
        if extras:
            values["extras"] = extras

        # Drop internal markers from identity comparison
        identity = {k: v for k, v in values.items() if k not in {"_missing_fields"}}
        if oid in by_id:
            prev = {k: v for k, v in by_id[oid].items() if k not in {"_missing_fields"}}
            if json.dumps(prev, sort_keys=True, default=str) == json.dumps(identity, sort_keys=True, default=str):
                issues.append(
                    Issue("warning", "duplicate_identical", f"duplicate identical order_id {oid}", row=i, field="order_id")
                )
                continue
            issues.append(
                Issue(
                    "error",
                    "duplicate_conflict",
                    f"duplicate order_id {oid} with differing fields — export must be order-level, not line-items",
                    row=i,
                    field="order_id",
                )
            )
            blocked = True
            continue
        by_id[oid] = values
        missing_map[oid] = list(values["_missing_fields"])

    records = list(by_id.values())
    if collect_issues:
        return records, missing_map, issues, blocked
    return records, missing_map


def _dry_run_tickets(
    table: StringTable,
    mapping: FieldMapping,
    issues: list[Issue],
    *,
    known_order_ids: set[str],
) -> DryRunResult:
    records, row_issues, blocked, unlinked = _normalize_tickets(
        table, mapping, known_order_ids=known_order_ids, collect_issues=True
    )
    issues.extend(row_issues)
    corpus_n = sum(1 for r in records if r.get("split") == "corpus")
    holdout_n = sum(1 for r in records if r.get("split") == "holdout")
    advisories: list[str] = []
    if 0 < corpus_n < MIN_USEFUL_TONE_CORPUS:
        advisories.append(
            f"weak:tone_corpus — {corpus_n} corpus tickets (< {MIN_USEFUL_TONE_CORPUS}); "
            "tone matching will be disabled until more examples are available"
        )
    elif corpus_n == 0:
        advisories.append("no corpus tickets — generation will use policy + transactions only")

    if unlinked and len(records) and (unlinked / max(len(records), 1)) > 0.5:
        issues.append(
            Issue(
                "warning",
                "majority_unlinked_orders",
                f"{unlinked}/{len(records)} tickets reference unknown order_ids",
            )
        )

    for f in ("ticket_id", "incoming_email", "actual_reply"):
        if not mapping.source_for(f):
            blocked = True
            issues.append(Issue("error", "required_unmapped", f"{f} is required", field=f))

    verdict: Verdict = "BLOCKED" if blocked else "READY"
    # Tickets themselves don't degrade recommended txn fields
    return DryRunResult(
        verdict=verdict,
        issues=issues,
        fill_rates=profile_table(table)["fill_rates"],
        counts={"rows": len(records), "corpus": corpus_n, "holdout": holdout_n, "unlinked_orders": unlinked},
        advisories=advisories,
        sample_rows=[{k: v for k, v in r.items() if k != "extras"} for r in records[:3]],
    )


def _normalize_tickets(
    table: StringTable,
    mapping: FieldMapping,
    *,
    known_order_ids: set[str],
    collect_issues: bool = False,
) -> Any:
    mapped = mapped_canonical_set(mapping)
    projected = project_rows(table, mapping)
    by_id: dict[str, dict[str, Any]] = {}
    issues: list[Issue] = []
    blocked = False
    unlinked = 0

    for i, raw in enumerate(projected, start=2):
        extras = raw.pop("_extras", {}) if isinstance(raw.get("_extras"), dict) else {}
        str_raw = {k: ("" if v is None else str(v)) for k, v in raw.items() if k != "_extras"}
        values, failures = apply_ticket_coercion(i, str_raw, mapped_fields=mapped)
        for f in failures:
            issues.append(f.as_issue())

        tid = values.get("ticket_id") or ""
        if not tid or not values.get("incoming_email") or not values.get("actual_reply"):
            issues.append(
                Issue("error", "required_empty", "ticket_id, incoming_email, and actual_reply are required", row=i)
            )
            blocked = True
            continue

        oid = values.get("order_id") or ""
        if oid and oid not in known_order_ids:
            extras = dict(extras or {})
            extras["source_order_id"] = oid
            values["order_id"] = ""
            unlinked += 1
            issues.append(
                Issue(
                    "warning",
                    "unknown_order_id",
                    f"ticket {tid} references unknown order_id {oid}; link cleared for tone-only use",
                    row=i,
                    field="order_id",
                )
            )
        if extras:
            values["extras"] = extras

        identity = {k: v for k, v in values.items()}
        if tid in by_id:
            prev = by_id[tid]
            if json.dumps(prev, sort_keys=True, default=str) == json.dumps(identity, sort_keys=True, default=str):
                issues.append(Issue("warning", "duplicate_identical", f"duplicate identical ticket_id {tid}", row=i))
                continue
            issues.append(
                Issue("error", "duplicate_conflict", f"duplicate ticket_id {tid} with differing fields", row=i)
            )
            blocked = True
            continue
        by_id[tid] = values

    records = list(by_id.values())
    if collect_issues:
        return records, issues, blocked, unlinked
    return records


def _dry_run_policy(path: str | Path) -> DryRunResult:
    from src.policy_ingest import extract_sections, normalize_sections

    path = Path(path)
    if not path.exists():
        return DryRunResult(
            verdict="BLOCKED",
            issues=[Issue("error", "missing_file", f"policy file not found: {path}")],
        )
    try:
        sections = extract_sections(path, use_llm_fallback=False)
        rules = normalize_sections(sections, use_llm_for_changed=False)
    except Exception as exc:
        return DryRunResult(
            verdict="BLOCKED",
            issues=[Issue("error", "policy_parse", f"{type(exc).__name__}: {exc}")],
        )
    if not rules:
        return DryRunResult(
            verdict="BLOCKED",
            issues=[Issue("error", "no_rules", "policy produced zero rules")],
            counts={"rules": 0, "sections": len(sections)},
        )
    return DryRunResult(
        verdict="READY",
        issues=[],
        counts={"rules": len(rules), "sections": len(sections)},
        sample_rows=[{"id": r.id, "category": r.category, "text": r.text[:200]} for r in rules[:3]],
    )


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
