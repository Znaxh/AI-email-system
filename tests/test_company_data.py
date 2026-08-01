"""Company-data ingestion tests — readers, mapping, coercion, verdicts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.company_data.coerce import coerce_date, coerce_money
from src.company_data.mapping import suggest_mapping
from src.company_data.profile import resolve_date_order, resolve_money_style
from src.company_data.readers import read_table
from src.company_data.schema import FieldMapping
from src.company_data.validate import dry_run_upload, normalize


@pytest.fixture()
def tmp_csv(tmp_path: Path):
    def _write(name: str, text: str) -> Path:
        p = tmp_path / name
        p.write_text(text)
        return p

    return _write


def test_csv_preserves_zero_padded_ids(tmp_csv):
    path = tmp_csv(
        "txns.csv",
        "order_id,customer_id,price,order_date,status\n"
        "00123,C1,10.00,2026-01-15,delivered\n",
    )
    table = read_table(path)
    assert table.rows[0][0] == "00123"


def test_json_preserves_long_numeric_ids(tmp_csv):
    path = tmp_csv(
        "txns.json",
        json.dumps(
            [
                {
                    "order_id": "9007199254740993",
                    "customer_id": "C1",
                    "price": "1.5",
                    "order_date": "2026-01-01",
                    "status": "delivered",
                }
            ]
        ),
    )
    table = read_table(path)
    assert table.as_dicts()[0]["order_id"] == "9007199254740993"


def test_date_order_column_level_and_ambiguous_block(tmp_csv):
    # All values ambiguous between DMY and MDY (day<=12)
    path = tmp_csv(
        "dates.csv",
        "order_id,order_date\nA,01/02/2026\nB,03/04/2026\n",
    )
    table = read_table(path)
    values = [r[1] for r in table.rows]
    assert resolve_date_order(values) is None

    # Disambiguating day>12 forces DMY
    path2 = tmp_csv(
        "dates2.csv",
        "order_id,order_date\nA,01/02/2026\nB,15/04/2026\n",
    )
    values2 = [r[1] for r in read_table(path2).rows]
    assert resolve_date_order(values2) == "DMY"

    mapping = FieldMapping(fields={"order_id": "order_id", "order_date": "order_date"})
    result = dry_run_upload(path, "transactions", mapping)
    assert result.verdict == "BLOCKED"
    assert any(i.code == "ambiguous_date" for i in result.issues)


def test_money_separator_disambiguation(tmp_csv):
    path = tmp_csv(
        "money.csv",
        "order_id,price\nA,\"1.234,56\"\nB,\"2,00\"\n",
    )
    values = [r[1] for r in read_table(path).rows]
    assert resolve_money_style(values) == "comma_decimal"
    assert coerce_money("1.234,56", style="comma_decimal") == pytest.approx(1234.56)


def test_genuine_zero_price_not_missing(tmp_csv):
    path = tmp_csv(
        "free.csv",
        "order_id,customer_id,price,order_date,status\n"
        "FREE-1,C1,0.0,2026-01-15,delivered\n",
    )
    mapping = FieldMapping(
        fields={
            "order_id": "order_id",
            "customer_id": "customer_id",
            "price": "price",
            "order_date": "order_date",
            "status": "status",
        },
        date_orders={"order_date": "YMD"},
        money_styles={"price": "dot_decimal"},
    )
    records, _ = normalize(path, "transactions", mapping, allow_degraded=True)
    assert records[0]["price"] == 0.0
    assert "price" not in records[0]["_missing_fields"]


def test_unmapped_price_is_degraded_and_marked_missing(tmp_csv):
    path = tmp_csv(
        "noprice.csv",
        "order_id,customer_id,order_date,status\n"
        "O1,C1,2026-01-15,delivered\n",
    )
    mapping = FieldMapping(
        fields={
            "order_id": "order_id",
            "customer_id": "customer_id",
            "order_date": "order_date",
            "status": "status",
            "price": None,
        },
        date_orders={"order_date": "YMD"},
    )
    result = dry_run_upload(path, "transactions", mapping)
    assert result.verdict == "DEGRADED"
    records, _ = normalize(path, "transactions", mapping, allow_degraded=True)
    assert "price" in records[0]["_missing_fields"]


def test_duplicate_identical_vs_conflict(tmp_csv):
    path = tmp_csv(
        "dup.csv",
        "order_id,customer_id,price,order_date,status\n"
        "O1,C1,10,2026-01-15,delivered\n"
        "O1,C1,10,2026-01-15,delivered\n",
    )
    mapping = FieldMapping(
        fields={
            "order_id": "order_id",
            "customer_id": "customer_id",
            "price": "price",
            "order_date": "order_date",
            "status": "status",
        },
        date_orders={"order_date": "YMD"},
        money_styles={"price": "dot_decimal"},
    )
    result = dry_run_upload(path, "transactions", mapping)
    assert result.verdict == "READY"
    assert any(i.code == "duplicate_identical" for i in result.issues)

    path2 = tmp_csv(
        "conflict.csv",
        "order_id,customer_id,price,order_date,status\n"
        "O1,C1,10,2026-01-15,delivered\n"
        "O1,C1,20,2026-01-15,delivered\n",
    )
    result2 = dry_run_upload(path2, "transactions", mapping)
    assert result2.verdict == "BLOCKED"
    assert any(i.code == "duplicate_conflict" for i in result2.issues)


def test_extras_preserved(tmp_csv):
    path = tmp_csv(
        "extras.csv",
        "order_id,customer_id,price,order_date,status,gift_note\n"
        "O1,C1,10,2026-01-15,delivered,happy birthday\n",
    )
    mapping = FieldMapping(
        fields={
            "order_id": "order_id",
            "customer_id": "customer_id",
            "price": "price",
            "order_date": "order_date",
            "status": "status",
        },
        date_orders={"order_date": "YMD"},
        money_styles={"price": "dot_decimal"},
    )
    records, _ = normalize(path, "transactions", mapping, allow_degraded=True)
    assert records[0]["extras"]["gift_note"] == "happy birthday"


def test_holdout_split_normalization(tmp_csv):
    path = tmp_csv(
        "tickets.csv",
        "ticket_id,incoming_email,actual_reply,split,order_id\n"
        "T1,hello,hi,holdout,O1\n"
        "T2,hello2,hi2,train,O1\n"
        "T3,hello3,hi3,weird,O1\n"
        "T4,hello4,hi4,,O1\n",
    )
    mapping = FieldMapping(
        fields={
            "ticket_id": "ticket_id",
            "incoming_email": "incoming_email",
            "actual_reply": "actual_reply",
            "split": "split",
            "order_id": "order_id",
        }
    )
    records, _ = normalize(path, "tickets", mapping, known_order_ids={"O1"}, allow_degraded=True)
    by_id = {r["ticket_id"]: r for r in records}
    assert by_id["T1"]["split"] == "holdout"
    assert by_id["T2"]["split"] == "corpus"
    assert by_id["T3"]["split"] == "corpus"  # unrecognized → corpus
    assert by_id["T4"]["split"] == "corpus"

    # Unmapped split column → all corpus
    mapping2 = FieldMapping(
        fields={
            "ticket_id": "ticket_id",
            "incoming_email": "incoming_email",
            "actual_reply": "actual_reply",
        }
    )
    records2, _ = normalize(path, "tickets", mapping2, known_order_ids=set(), allow_degraded=True)
    assert all(r["split"] == "corpus" for r in records2)


def test_unknown_order_link_cleared(tmp_csv):
    path = tmp_csv(
        "tickets.csv",
        "ticket_id,incoming_email,actual_reply,order_id\n"
        "T1,hello,hi,MISSING\n"
        "T2,hello2,hi2,OK\n",
    )
    mapping = FieldMapping(
        fields={
            "ticket_id": "ticket_id",
            "incoming_email": "incoming_email",
            "actual_reply": "actual_reply",
            "order_id": "order_id",
        }
    )
    result = dry_run_upload(path, "tickets", mapping, known_order_ids={"OK"})
    assert result.verdict == "READY"
    records, _ = normalize(path, "tickets", mapping, known_order_ids={"OK"}, allow_degraded=True)
    by_id = {r["ticket_id"]: r for r in records}
    assert by_id["T1"]["order_id"] == ""
    assert by_id["T1"]["extras"]["source_order_id"] == "MISSING"
    assert by_id["T2"]["order_id"] == "OK"


def test_suggested_mapping_aliases(tmp_csv):
    path = tmp_csv(
        "alias.csv",
        "Order Number,Customer Email,Order Total,Purchase Date,State\n"
        "O1,a@b.com,12.5,2026-01-01,delivered\n",
    )
    table = read_table(path)
    suggested = suggest_mapping(table, "transactions")
    assert suggested.fields["order_id"] == "Order Number"
    assert suggested.fields["customer_id"] == "Customer Email"
    assert suggested.fields["price"] == "Order Total"


def test_coerce_date_ymd():
    assert coerce_date("2026-01-15", order="YMD") == "2026-01-15"
