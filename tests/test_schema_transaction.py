"""Transaction schema defaults — safe construction after DEGRADED uploads."""

from src.schema import Transaction


def test_transaction_requires_only_order_id():
    txn = Transaction(order_id="O1")
    assert txn.order_id == "O1"
    assert txn.customer_id == ""
    assert txn.product == "(unspecified product)"
    assert txn.price == 0.0
    assert txn.order_date == ""
    assert txn.status == ""


def test_transaction_accepts_genuine_zero_price():
    txn = Transaction(order_id="FREE", price=0.0, customer_id="C1", status="delivered")
    assert txn.price == 0.0
    # Defaults are sentinels for construction only — missingness is external.
    assert txn.model_dump()["price"] == 0.0
