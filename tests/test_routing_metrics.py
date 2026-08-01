from src.company_data import routing_metrics


def test_routing_metrics_auto_rate_and_flag_counts():
    routing_metrics.reset()
    routing_metrics.record("auto", [])
    routing_metrics.record("review", ["unverifiable:order_value"])
    routing_metrics.record("review", ["unverifiable:order_value", "order_id_not_referenced (-4)"])
    routing_metrics.record("escalate", ["unverifiable:no_transaction"])
    snap = routing_metrics.snapshot()
    assert snap["n"] == 4
    assert snap["auto"] == 1
    assert snap["auto_rate"] == 0.25
    assert snap["by_flag"]["unverifiable:order_value"] == 2
    assert snap["by_flag"]["unverifiable:no_transaction"] == 1
    assert "order_id_not_referenced (-4)" not in snap["by_flag"]
