"""Canonical company-data field definitions, aliases, and verdict models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MIN_USEFUL_TONE_CORPUS = 12

Target = Literal["transactions", "tickets", "policy"]
Verdict = Literal["READY", "DEGRADED", "BLOCKED"]
DateOrder = Literal["DMY", "MDY", "YMD"]
MoneyStyle = Literal["dot_decimal", "comma_decimal"]

RECOMMENDED_TXN_FIELDS = ("customer_id", "order_date", "price", "status")
REQUIRED_TXN_FIELDS = ("order_id",)
REQUIRED_TICKET_FIELDS = ("ticket_id", "incoming_email", "actual_reply")
OPTIONAL_TXN_FIELDS = (
    "product",
    "delivery_date",
    "promised_delivery_date",
    "final_sale",
    "returns_last_90_days",
)
OPTIONAL_TICKET_FIELDS = ("split", "order_id", "category", "sentiment")

# Source/UI aliases → canonical runtime field names in src.schema.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "order_id": ("order_id", "orderid", "order", "order_number", "ordernumber", "order_no"),
    "customer_id": (
        "customer_id",
        "customerid",
        "customer",
        "customer_email",
        "customeremail",
        "email",
        "buyer_email",
        "buyer_id",
    ),
    "price": ("price", "order_total", "ordertotal", "total", "amount", "order_amount"),
    "product": ("product", "product_name", "productname", "item", "sku_name", "title"),
    "order_date": ("order_date", "orderdate", "purchased_at", "purchase_date", "created_at"),
    "delivery_date": ("delivery_date", "deliverydate", "delivered_at", "shipped_date"),
    "promised_delivery_date": (
        "promised_delivery_date",
        "promised_date",
        "eta",
        "expected_delivery",
    ),
    "status": ("status", "order_status", "fulfilment_state", "fulfillment_status", "state"),
    "final_sale": ("final_sale", "finalsale", "is_final_sale"),
    "returns_last_90_days": ("returns_last_90_days", "returns_90d", "return_count"),
    "ticket_id": ("ticket_id", "ticketid", "id", "case_id", "conversation_id"),
    "incoming_email": (
        "incoming_email",
        "customer_message",
        "customer_email_body",
        "email_body",
        "message",
        "inquiry",
    ),
    "actual_reply": (
        "actual_reply",
        "agent_reply",
        "reply",
        "response",
        "agent_response",
        "sent_reply",
    ),
    "split": ("split", "dataset_split", "set", "partition"),
    "category": ("category", "intent", "topic", "type"),
    "sentiment": ("sentiment", "tone", "emotion"),
    "created_at": ("created_at", "ticket_created_at", "opened_at"),
}

HOLDOUT_LABELS = frozenset({"holdout", "test", "eval", "evaluation", "validation"})
CORPUS_LABELS = frozenset({"corpus", "train", "training", "prod", "production"})

FIELD_FLAG_MAP = {
    "customer_id": "unverifiable:identity",
    "order_date": "unverifiable:time_window",
    "price": "unverifiable:order_value",
    "status": "unverifiable:fulfilment_state",
}

TXN_CANONICAL_FIELDS = (
    REQUIRED_TXN_FIELDS
    + RECOMMENDED_TXN_FIELDS
    + OPTIONAL_TXN_FIELDS
)
TICKET_CANONICAL_FIELDS = REQUIRED_TICKET_FIELDS + OPTIONAL_TICKET_FIELDS


@dataclass
class Issue:
    level: Literal["error", "warning", "info"]
    code: str
    message: str
    row: int | None = None
    field: str | None = None


@dataclass
class FieldMapping:
    """Canonical-keyed mapping: each canonical field → source column (or None)."""

    fields: dict[str, str | None] = field(default_factory=dict)
    date_orders: dict[str, DateOrder] = field(default_factory=dict)
    money_styles: dict[str, MoneyStyle] = field(default_factory=dict)
    sheet: str | None = None

    def source_for(self, canonical: str) -> str | None:
        return self.fields.get(canonical)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": dict(self.fields),
            "date_orders": dict(self.date_orders),
            "money_styles": dict(self.money_styles),
            "sheet": self.sheet,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FieldMapping":
        data = data or {}
        return cls(
            fields=dict(data.get("fields") or {}),
            date_orders=dict(data.get("date_orders") or {}),
            money_styles=dict(data.get("money_styles") or {}),
            sheet=data.get("sheet"),
        )


@dataclass
class DryRunResult:
    verdict: Verdict
    issues: list[Issue] = field(default_factory=list)
    fill_rates: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    capability_impact: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "issues": [
                {
                    "level": i.level,
                    "code": i.code,
                    "message": i.message,
                    "row": i.row,
                    "field": i.field,
                }
                for i in self.issues
            ],
            "fill_rates": self.fill_rates,
            "counts": self.counts,
            "capability_impact": self.capability_impact,
            "advisories": self.advisories,
            "sample_rows": self.sample_rows,
        }


@dataclass
class PreviewResult:
    columns: list[str]
    samples: list[dict[str, str]]
    suggested_mapping: FieldMapping
    sheets: list[str] = field(default_factory=list)
    fill_rates: dict[str, float] = field(default_factory=dict)
    date_candidates: dict[str, list[str]] = field(default_factory=dict)
    money_candidates: dict[str, list[str]] = field(default_factory=dict)
    file_hash: str = ""
    target: Target = "transactions"
    row_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "samples": self.samples,
            "suggested_mapping": self.suggested_mapping.to_dict(),
            "sheets": self.sheets,
            "fill_rates": self.fill_rates,
            "date_candidates": self.date_candidates,
            "money_candidates": self.money_candidates,
            "file_hash": self.file_hash,
            "target": self.target,
            "row_count": self.row_count,
        }


def normalize_header(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in (name or "").strip().lower()).strip("_")
