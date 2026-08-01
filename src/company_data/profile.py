"""Column profiling for upload preview — fill rates, samples, date/money candidates."""

from __future__ import annotations

import re
from collections import Counter

from src.company_data.readers import StringTable
from src.company_data.schema import DateOrder, MoneyStyle

_DATE_TOKEN = re.compile(
    r"^(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})$|^(\d{4})-(\d{2})-(\d{2})"
)
_MONEY_TOKEN = re.compile(r"^[$€£]?\s*-?\d{1,3}([.,]\d{3})*([.,]\d{1,2})?$|^[$€£]?\s*-?\d+([.,]\d+)?$")


def profile_table(table: StringTable, *, sample_n: int = 5) -> dict:
    fill: dict[str, float] = {}
    date_candidates: dict[str, list[str]] = {}
    money_candidates: dict[str, list[str]] = {}
    n = max(table.row_count, 1)
    for col_i, header in enumerate(table.headers):
        values = [row[col_i] for row in table.rows if col_i < len(row)]
        nonempty = [v for v in values if str(v).strip()]
        fill[header] = len(nonempty) / n
        orders = infer_date_orders(nonempty)
        if orders:
            date_candidates[header] = list(orders)
        styles = infer_money_styles(nonempty)
        if styles:
            money_candidates[header] = list(styles)
    samples = table.as_dicts()[:sample_n]
    return {
        "columns": list(table.headers),
        "samples": samples,
        "fill_rates": fill,
        "date_candidates": date_candidates,
        "money_candidates": money_candidates,
        "row_count": table.row_count,
        "sheets": list(table.sheets),
        "sheet": table.sheet,
        "warnings": list(table.warnings),
    }


def infer_date_orders(values: list[str]) -> list[DateOrder] | None:
    """Return a singleton order when disambiguated; multiple when ambiguous; None if not dates."""
    parsed_orders: list[set[DateOrder]] = []
    dateish = 0
    for raw in values:
        v = raw.strip()
        if not v:
            continue
        opts = _possible_date_orders(v)
        if not opts:
            continue
        dateish += 1
        parsed_orders.append(opts)
    if dateish < max(1, int(0.3 * max(1, len([v for v in values if v.strip()])))):
        return None
    # Intersection of possible orders across all values.
    common: set[DateOrder] | None = None
    for opts in parsed_orders:
        common = opts if common is None else common & opts
    if not common:
        # No shared interpretation — ambiguous / conflict.
        return ["DMY", "MDY", "YMD"]  # type: ignore[return-value]
    return sorted(common)  # type: ignore[return-value]


def resolve_date_order(values: list[str], *, forced: DateOrder | None = None) -> DateOrder | None:
    if forced:
        return forced
    candidates = infer_date_orders(values)
    if candidates is None:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return None  # ambiguous — caller must ask user


def infer_money_styles(values: list[str]) -> list[MoneyStyle] | None:
    nonempty = [v.strip() for v in values if v.strip()]
    if not nonempty:
        return None
    moneyish = [v for v in nonempty if _looks_money(v)]
    if len(moneyish) < max(1, int(0.3 * len(nonempty))):
        return None
    styles: set[MoneyStyle] = set()
    for v in moneyish:
        s = _possible_money_style(v)
        if s:
            styles |= s
    # Look for a disambiguating value.
    disambiguated: set[MoneyStyle] | None = None
    for v in moneyish:
        opts = _possible_money_style(v)
        if opts and len(opts) == 1:
            disambiguated = opts if disambiguated is None else disambiguated & opts
    if disambiguated and len(disambiguated) == 1:
        return sorted(disambiguated)  # type: ignore[return-value]
    if len(styles) == 1:
        return sorted(styles)  # type: ignore[return-value]
    if styles:
        return sorted(styles)  # type: ignore[return-value]
    return None


def resolve_money_style(values: list[str], *, forced: MoneyStyle | None = None) -> MoneyStyle | None:
    if forced:
        return forced
    candidates = infer_money_styles(values)
    if candidates is None:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return None


def _possible_date_orders(value: str) -> set[DateOrder]:
    v = value.strip()
    # ISO YMD
    if re.match(r"^\d{4}-\d{2}-\d{2}", v):
        return {"YMD"}
    m = re.match(r"^(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})$", v)
    if not m:
        return set()
    a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
    opts: set[DateOrder] = set()
    # YMD: a is year
    if a >= 1000 and 1 <= b <= 12 and 1 <= c <= 31:
        opts.add("YMD")
    # DMY: a=day, b=month, c=year
    if 1 <= a <= 31 and 1 <= b <= 12 and (c >= 1000 or c <= 99):
        opts.add("DMY")
    # MDY: a=month, b=day, c=year
    if 1 <= a <= 12 and 1 <= b <= 31 and (c >= 1000 or c <= 99):
        opts.add("MDY")
    # Disambiguate: day > 12 forces order
    if a > 12 and b <= 12:
        opts.discard("MDY")
        if "YMD" in opts and a < 1000:
            opts.discard("YMD")
    if b > 12 and a <= 12:
        opts.discard("DMY")
    return opts


def _looks_money(value: str) -> bool:
    v = value.strip().replace(" ", "")
    return bool(_MONEY_TOKEN.match(v))


def _possible_money_style(value: str) -> set[MoneyStyle]:
    v = value.strip().replace(" ", "").replace("$", "").replace("€", "").replace("£", "")
    if not v or v in {"-", "."}:
        return set()
    if "," in v and "." in v:
        # Last separator is decimal.
        if v.rfind(",") > v.rfind("."):
            return {"comma_decimal"}
        return {"dot_decimal"}
    if "," in v:
        parts = v.split(",")
        if len(parts[-1]) == 3 and all(p.isdigit() or (i == 0 and p.lstrip("-").isdigit()) for i, p in enumerate(parts)):
            # Could be thousands separator with implied dot decimal, or ambiguous.
            return {"dot_decimal", "comma_decimal"}
        if len(parts[-1]) <= 2:
            return {"comma_decimal"}
        return {"comma_decimal", "dot_decimal"}
    if "." in v:
        parts = v.split(".")
        if len(parts[-1]) == 3 and all(p.lstrip("-").isdigit() for p in parts):
            return {"dot_decimal", "comma_decimal"}
        if len(parts[-1]) <= 2:
            return {"dot_decimal"}
        return {"dot_decimal"}
    if v.lstrip("-").isdigit():
        return {"dot_decimal", "comma_decimal"}
    return set()
