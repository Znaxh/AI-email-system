"""PolicyRule depends_on inference at ingest."""

from src.policy_ingest import _infer_depends_on, normalize_rule, RawSection


def test_infer_price_and_window():
    deps, status = _infer_depends_on(
        "order total exceeds $200",
        "MUST escalate",
        "Any case exceeding $200 MUST be escalated.",
    )
    assert "price" in deps
    assert status == "resolved"

    deps2, status2 = _infer_depends_on(
        "return within 30 days of purchase",
        "MUST refund",
        "A return within 30 days MUST be granted a full refund.",
    )
    assert "order_date" in deps2
    assert status2 == "resolved"


def test_unconditional_polite_rule():
    deps, status = _infer_depends_on("", "MUST thank the customer", "Agents MUST thank the customer.")
    assert deps == []
    assert status == "resolved"


def test_normalize_rule_sets_depends_on():
    section = RawSection(
        heading="R9 High value",
        body="Any case exceeding $200 MUST be escalated to a human senior agent.",
    )
    rule = normalize_rule(section, use_llm=False)
    assert "price" in rule.depends_on
    assert rule.dependency_status == "resolved"
