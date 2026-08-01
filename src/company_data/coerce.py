"""Explicit typed coercion with row-numbered failures — never silent inference."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from src.company_data.schema import DateOrder, Issue, MoneyStyle


@dataclass
class CoerceFailure:
    row: int
    field: str
    value: str
    message: str

    def as_issue(self) -> Issue:
        return Issue(
            level="error",
            code="coerce_failed",
            message=f"{self.field}: {self.message} (value={self.value!r})",
            row=self.row,
            field=self.field,
        )


def coerce_bool(raw: str) -> bool | CoerceFailure:
    v = (raw or "").strip().lower()
    if v in {"", "0", "false", "no", "n", "f"}:
        return False
    if v in {"1", "true", "yes", "y", "t"}:
        return True
    return CoerceFailure(0, "bool", raw, "not a boolean")


def coerce_int(raw: str, *, default: int | None = None) -> int | CoerceFailure:
    v = (raw or "").strip()
    if not v:
        return default if default is not None else CoerceFailure(0, "int", raw, "empty")
    try:
        return int(v)
    except ValueError:
        return CoerceFailure(0, "int", raw, "not an integer")


def coerce_money(
    raw: str,
    *,
    style: MoneyStyle,
    allow_empty: bool = True,
) -> float | None | CoerceFailure:
    v = (raw or "").strip()
    if not v:
        return None if allow_empty else CoerceFailure(0, "price", raw, "empty")
    v = v.replace(" ", "").replace("$", "").replace("€", "").replace("£", "")
    try:
        if style == "comma_decimal":
            # 1.234,56 → 1234.56
            if "." in v and "," in v:
                v = v.replace(".", "").replace(",", ".")
            else:
                v = v.replace(",", ".")
        else:
            # dot_decimal: 1,234.56 → 1234.56
            if "," in v and "." in v:
                v = v.replace(",", "")
            elif "," in v and "." not in v:
                # Ambiguous single-separator left for style resolution; treat comma as thousands.
                parts = v.split(",")
                if len(parts[-1]) == 3:
                    v = v.replace(",", "")
                else:
                    return CoerceFailure(0, "price", raw, "comma present under dot_decimal style")
        return float(v)
    except ValueError:
        return CoerceFailure(0, "price", raw, "not a number")


def coerce_date(
    raw: str,
    *,
    order: DateOrder,
    allow_empty: bool = True,
) -> str | None | CoerceFailure:
    """Return ISO YYYY-MM-DD or None/failure."""
    v = (raw or "").strip()
    if not v:
        return None if allow_empty else CoerceFailure(0, "date", raw, "empty")
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}", v):
        try:
            datetime.strptime(v[:10], "%Y-%m-%d")
            return v[:10]
        except ValueError:
            return CoerceFailure(0, "date", raw, "invalid ISO date")
    m = re.match(r"^(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})$", v)
    if not m:
        return CoerceFailure(0, "date", raw, "unrecognized date format")
    a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        if order == "YMD":
            y, mo, d = a, b, c
        elif order == "DMY":
            d, mo, y = a, b, c
        else:  # MDY
            mo, d, y = a, b, c
        if y < 100:
            y += 2000 if y < 70 else 1900
        return date(y, mo, d).isoformat()
    except ValueError:
        return CoerceFailure(0, "date", raw, f"invalid {order} date")


def with_row(failure: CoerceFailure, row: int, field: str) -> CoerceFailure:
    return CoerceFailure(row=row, field=field, value=failure.value, message=failure.message)


def apply_txn_coercion(
    row_num: int,
    raw: dict[str, str],
    *,
    date_orders: dict[str, DateOrder],
    money_styles: dict[str, MoneyStyle],
    mapped_fields: set[str],
) -> tuple[dict[str, Any], list[str], list[CoerceFailure]]:
    """Coerce one transaction row. Returns (values, missing_fields, failures)."""
    out: dict[str, Any] = {}
    missing: list[str] = []
    failures: list[CoerceFailure] = []

    def present(field: str) -> bool:
        return field in mapped_fields and bool(str(raw.get(field, "")).strip())

    # order_id — string, required by caller
    out["order_id"] = str(raw.get("order_id", "")).strip()

    for field in ("customer_id", "status", "product"):
        if field not in mapped_fields:
            missing.append(field)
            out[field] = "" if field != "product" else "(unspecified product)"
        elif not str(raw.get(field, "")).strip():
            missing.append(field)
            out[field] = "" if field != "product" else "(unspecified product)"
        else:
            out[field] = str(raw.get(field, "")).strip()

    # price
    if "price" not in mapped_fields:
        missing.append("price")
        out["price"] = 0.0
    else:
        raw_price = str(raw.get("price", "")).strip()
        if not raw_price:
            missing.append("price")
            out["price"] = 0.0
        else:
            style = money_styles.get("price") or money_styles.get(
                next(iter(money_styles), ""), "dot_decimal"  # type: ignore[arg-type]
            )
            if not isinstance(style, str):
                style = "dot_decimal"
            # Prefer field-keyed style
            style = money_styles.get("price", "dot_decimal")  # type: ignore[assignment]
            coerced = coerce_money(raw_price, style=style, allow_empty=False)  # type: ignore[arg-type]
            if isinstance(coerced, CoerceFailure):
                failures.append(with_row(coerced, row_num, "price"))
                out["price"] = 0.0
                missing.append("price")
            else:
                out["price"] = float(coerced)  # type: ignore[arg-type]

    # dates
    for field in ("order_date", "delivery_date", "promised_delivery_date"):
        if field not in mapped_fields:
            if field == "order_date":
                missing.append(field)
            out[field] = "" if field == "order_date" else None
            continue
        raw_v = str(raw.get(field, "")).strip()
        if not raw_v:
            if field == "order_date":
                missing.append(field)
            out[field] = "" if field == "order_date" else None
            continue
        order = date_orders.get(field)
        if not order:
            failures.append(
                CoerceFailure(row_num, field, raw_v, "date order not resolved for column")
            )
            if field == "order_date":
                missing.append(field)
            out[field] = "" if field == "order_date" else None
            continue
        coerced = coerce_date(raw_v, order=order, allow_empty=False)
        if isinstance(coerced, CoerceFailure):
            failures.append(with_row(coerced, row_num, field))
            if field == "order_date":
                missing.append(field)
            out[field] = "" if field == "order_date" else None
        else:
            out[field] = coerced

    # optional bools / ints
    if "final_sale" in mapped_fields and str(raw.get("final_sale", "")).strip():
        b = coerce_bool(raw["final_sale"])
        if isinstance(b, CoerceFailure):
            failures.append(with_row(b, row_num, "final_sale"))
            out["final_sale"] = False
        else:
            out["final_sale"] = b
    else:
        out["final_sale"] = False

    if "returns_last_90_days" in mapped_fields and str(raw.get("returns_last_90_days", "")).strip():
        iv = coerce_int(raw["returns_last_90_days"], default=0)
        if isinstance(iv, CoerceFailure):
            failures.append(with_row(iv, row_num, "returns_last_90_days"))
            out["returns_last_90_days"] = 0
        else:
            out["returns_last_90_days"] = iv
    else:
        out["returns_last_90_days"] = 0

    # Deduplicate missing while preserving order
    seen = set()
    missing_u = []
    for m in missing:
        if m not in seen:
            seen.add(m)
            missing_u.append(m)
    return out, missing_u, failures


def apply_ticket_coercion(
    row_num: int,
    raw: dict[str, str],
    *,
    mapped_fields: set[str],
) -> tuple[dict[str, Any], list[CoerceFailure]]:
    from src.company_data.schema import CORPUS_LABELS, HOLDOUT_LABELS

    out: dict[str, Any] = {}
    failures: list[CoerceFailure] = []
    out["ticket_id"] = str(raw.get("ticket_id", "")).strip()
    out["incoming_email"] = str(raw.get("incoming_email", "")).strip()
    out["actual_reply"] = str(raw.get("actual_reply", "")).strip()
    out["order_id"] = str(raw.get("order_id", "")).strip() if "order_id" in mapped_fields else ""
    out["category"] = (
        str(raw.get("category", "")).strip() or "uncategorized"
        if "category" in mapped_fields
        else "uncategorized"
    )
    out["sentiment"] = (
        str(raw.get("sentiment", "")).strip() or "neutral"
        if "sentiment" in mapped_fields
        else "neutral"
    )
    if "split" in mapped_fields:
        raw_split = str(raw.get("split", "")).strip().lower()
        if raw_split in HOLDOUT_LABELS:
            out["split"] = "holdout"
        elif raw_split in CORPUS_LABELS or not raw_split:
            out["split"] = "corpus"
        else:
            # Unrecognized → corpus (holdout never accidental)
            out["split"] = "corpus"
    else:
        out["split"] = "corpus"
    return out, failures
