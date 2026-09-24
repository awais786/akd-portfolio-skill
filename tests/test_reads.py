from __future__ import annotations

from datetime import UTC, datetime, timedelta

from akd import reads

NOW = lambda: datetime.now(UTC)


def test_no_snapshot_is_null_never_empty(store):
    for fn, field in ((reads.holdings, "positions"), (reads.orders, "orders"), (reads.cash, "cash")):
        out = fn(store, "u")
        assert out["data"][field] is None
        assert out["data"]["broker_connected"] is False
        assert "no_snapshot" in out["flags"]


def test_holdings_live_with_cost_totals(store):
    store.put("u", "GetCollateral", NOW(), {"positions": [
        {"symbol": "OGDC", "quantity": 100, "avg_cost": 85.5},
        {"symbol": "XYZ", "quantity": 10, "avg_cost": None}]})
    out = reads.holdings(store, "u")
    assert out["data"]["broker_connected"] is True
    assert out["data"]["totals"] == {"position_count": 2, "total_cost_value": 8550.0, "positions_without_cost": 1}
    assert out["flags"] == []


def test_old_content_confirmed_recently_is_live(store):
    """Unchanged holdings are normal: as_of old, last_confirmed_at fresh => connected."""
    payload = {"positions": [{"symbol": "OGDC", "quantity": 1, "avg_cost": 1.0}]}
    store.put("u", "GetCollateral", NOW() - timedelta(hours=3), payload)
    store.put("u", "GetCollateral", NOW(), payload)
    assert reads.holdings(store, "u")["data"]["broker_connected"] is True


def test_stale_when_not_confirmed_for_over_fifteen_minutes(store):
    store.put("u", "GetCollateral", NOW() - timedelta(minutes=30), {"positions": []})
    out = reads.holdings(store, "u")
    assert out["data"]["broker_connected"] is False
    assert "stale_snapshot" in out["flags"]


def test_future_stamped_snapshot_is_clock_skew_not_live(store):
    store.put("u", "GetCollateral", NOW() + timedelta(hours=1), {"positions": []})
    out = reads.holdings(store, "u")
    assert out["data"]["broker_connected"] is False
    assert "clock_skew" in out["flags"]


def test_empty_book_vs_unreadable(store):
    store.put("u", "GetCollateral", NOW(), {"positions": []})
    out = reads.holdings(store, "u")
    assert out["data"]["positions"] == [] and "empty_book" in out["flags"]

    store.put("u", "GetOrderHistory", NOW(), {"orders": "oops"})
    out = reads.orders(store, "u")
    assert out["data"]["orders"] is None
    assert out["data"]["broker_connected"] is False
    assert "payload_unreadable" in out["flags"]


def test_orders_always_flagged_unverified(store):
    store.put("u", "GetOrderHistory", NOW(), {"orders": []})
    assert "decoder_unverified" in reads.orders(store, "u")["flags"]


def test_cash_reads_exposure_snapshot(store):
    store.put("u", "GetExposureDynamic", NOW(), {"cash_pkr": 1000.0, "free_cash_pkr": 900.0})
    out = reads.cash(store, "u")
    assert out["data"]["cash"]["free_cash_pkr"] == 900.0 and out["data"]["broker_connected"] is True


def test_status_summarises_operations(store):
    store.put("u", "GetCollateral", NOW(), {"positions": []})
    out = reads.status(store, "u", {"state": "running"})
    assert "GetCollateral" in out["data"]["operations_seen"]
    assert out["data"]["broker_connected"] is True


def test_status_not_connected_when_daemon_stopped_even_if_data_is_fresh(store):
    store.put("u", "GetCollateral", NOW(), {"positions": []})
    out = reads.status(store, "u", {"state": "stopped"})
    assert out["data"]["broker_connected"] is False
