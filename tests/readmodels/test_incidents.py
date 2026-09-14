from virtual_orders.readmodels.incidents import ErrorCategory, OrderErrorGroup, classify_order_errors


def test_errors_are_grouped_by_type_with_orders_and_runs():
    groups = classify_order_errors({
        "run-2": {"o1": "ERROR:KeyError", "o3": "ERROR:OperationalError", "o4": "PROJECTION_MISSING"},
        "run-1": {"o1": "ERROR:KeyError", "o2": "ERROR:KeyError"},
    })
    assert groups == [
        OrderErrorGroup("OperationalError", ErrorCategory.INFRASTRUCTURE, 1, 1, ("o3",), ("run-2",)),
        OrderErrorGroup("KeyError", ErrorCategory.PROGRAMMING, 3, 2, ("o1", "o2"), ("run-1", "run-2")),
    ]


def test_integrity_kinds_and_empty_inputs_yield_no_groups():
    assert classify_order_errors({}) == []
    assert classify_order_errors({"run": {"o": "EVENT_HASH_CONFLICT"}}) == []
