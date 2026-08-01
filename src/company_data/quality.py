"""Per-email data-quality flags — never file-wide, never inferred from sentinels."""

from __future__ import annotations

from typing import Iterable

from src.company_data.schema import FIELD_FLAG_MAP, RECOMMENDED_TXN_FIELDS
from src.schema import GeneratedReply, PolicyRule, Transaction


def compute_email_quality_flags(
    *,
    used_placeholder: bool,
    order_id: str,
    missing_fields: frozenset[str] | set[str] | None,
    gen: GeneratedReply,
    rules_by_id: dict[str, PolicyRule],
) -> list[str]:
    """Return per-email unverifiable:* flags for the router.

    A field-specific flag is emitted iff the selected transaction marks the field
    missing AND at least one cited/retrieved rule depends on that field.
    """
    if used_placeholder:
        # Distinct no-order path — never also emit field-specific missingness flags.
        return ["unverifiable:no_transaction"]

    missing = set(missing_fields or ())
    if not missing:
        return []

    used_ids = _used_rule_ids(gen)
    if not used_ids:
        # No rules used — nothing to gate on field dependency.
        return []

    flags: list[str] = []
    unknown_deps = False
    dependent_fields: set[str] = set()

    for rid in used_ids:
        rule = rules_by_id.get(rid.upper()) or rules_by_id.get(rid)
        if rule is None:
            unknown_deps = True
            continue
        status = getattr(rule, "dependency_status", "resolved") or "resolved"
        deps = list(getattr(rule, "depends_on", None) or [])
        if status == "unknown":
            unknown_deps = True
            continue
        dependent_fields.update(deps)

    for field in RECOMMENDED_TXN_FIELDS:
        if field in missing and field in dependent_fields:
            flag = FIELD_FLAG_MAP.get(field)
            if flag and flag not in flags:
                flags.append(flag)

    if unknown_deps and missing.intersection(RECOMMENDED_TXN_FIELDS):
        if "unverifiable:rule_dependencies" not in flags:
            flags.append("unverifiable:rule_dependencies")

    return flags


def _used_rule_ids(gen: GeneratedReply) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for rid in list(gen.cited_rule_ids or []) + list(gen.retrieved_rule_ids or []):
        key = str(rid or "").strip().upper()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    # Also include remedy.rule_cited
    rc = str(getattr(gen.remedy, "rule_cited", "") or "").strip().upper()
    if rc and rc not in seen:
        out.append(rc)
    return out


def rules_index(rules: Iterable[PolicyRule]) -> dict[str, PolicyRule]:
    return {r.id.upper(): r for r in rules}
