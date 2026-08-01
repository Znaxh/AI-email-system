"""Deterministic alias-based mapping suggestions — never auto-committed."""

from __future__ import annotations

from src.company_data.profile import infer_date_orders, infer_money_styles, profile_table
from src.company_data.readers import StringTable
from src.company_data.schema import (
    FIELD_ALIASES,
    OPTIONAL_TICKET_FIELDS,
    OPTIONAL_TXN_FIELDS,
    RECOMMENDED_TXN_FIELDS,
    REQUIRED_TICKET_FIELDS,
    REQUIRED_TXN_FIELDS,
    TICKET_CANONICAL_FIELDS,
    TXN_CANONICAL_FIELDS,
    FieldMapping,
    Target,
    normalize_header,
)


def suggest_mapping(table: StringTable, target: Target) -> FieldMapping:
    headers = list(table.headers)
    norm_to_header = {normalize_header(h): h for h in headers}
    canonical = (
        list(TXN_CANONICAL_FIELDS) if target == "transactions" else list(TICKET_CANONICAL_FIELDS)
    )
    fields: dict[str, str | None] = {c: None for c in canonical}
    used: set[str] = set()
    for canon in canonical:
        aliases = FIELD_ALIASES.get(canon, (canon,))
        for alias in aliases:
            key = normalize_header(alias)
            if key in norm_to_header and norm_to_header[key] not in used:
                fields[canon] = norm_to_header[key]
                used.add(norm_to_header[key])
                break
    mapping = FieldMapping(fields=fields, sheet=table.sheet)
    # Suggest date/money conventions for mapped columns when unambiguous.
    for canon, src in fields.items():
        if not src:
            continue
        col_i = headers.index(src)
        values = [row[col_i] for row in table.rows if col_i < len(row)]
        if canon in ("order_date", "delivery_date", "promised_delivery_date"):
            orders = infer_date_orders(values)
            if orders and len(orders) == 1:
                mapping.date_orders[canon] = orders[0]  # type: ignore[assignment]
        if canon == "price":
            styles = infer_money_styles(values)
            if styles and len(styles) == 1:
                mapping.money_styles[canon] = styles[0]  # type: ignore[assignment]
    return mapping


def validate_mapping(mapping: FieldMapping, target: Target, columns: list[str]) -> list[str]:
    """Return human-readable mapping problems (empty = ok structurally)."""
    errors: list[str] = []
    required = REQUIRED_TXN_FIELDS if target == "transactions" else REQUIRED_TICKET_FIELDS
    colset = set(columns)
    for field in required:
        src = mapping.source_for(field)
        if not src:
            errors.append(f"required field {field!r} is unmapped")
        elif src not in colset:
            errors.append(f"mapped source column {src!r} for {field!r} not in file")
    for field, src in mapping.fields.items():
        if src and src not in colset:
            errors.append(f"mapped source column {src!r} for {field!r} not in file")
    # One source column must not map to two canonical fields.
    reverse: dict[str, list[str]] = {}
    for field, src in mapping.fields.items():
        if src:
            reverse.setdefault(src, []).append(field)
    for src, targets in reverse.items():
        if len(targets) > 1:
            errors.append(f"source column {src!r} mapped to multiple fields: {targets}")
    return errors


def mapped_canonical_set(mapping: FieldMapping) -> set[str]:
    return {k for k, v in mapping.fields.items() if v}


def project_rows(table: StringTable, mapping: FieldMapping) -> list[dict[str, str]]:
    """Project source rows onto canonical keys; extras hold unmapped columns."""
    used_sources = {v for v in mapping.fields.values() if v}
    out = []
    for row in table.as_dicts():
        projected: dict[str, str] = {}
        for canon, src in mapping.fields.items():
            projected[canon] = row.get(src, "") if src else ""
        extras = {k: v for k, v in row.items() if k not in used_sources}
        if extras:
            # stash as JSON-ish flat keys under extras_* for later merge
            projected["_extras"] = extras  # type: ignore[assignment]
        out.append(projected)  # type: ignore[arg-type]
    return out  # type: ignore[return-value]


def canonical_fields_for(target: Target) -> list[str]:
    if target == "transactions":
        return list(REQUIRED_TXN_FIELDS) + list(RECOMMENDED_TXN_FIELDS) + list(OPTIONAL_TXN_FIELDS)
    if target == "tickets":
        return list(REQUIRED_TICKET_FIELDS) + list(OPTIONAL_TICKET_FIELDS)
    return []
