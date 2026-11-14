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
    t = out["data"]["totals"]
    assert (t["position_count"], t["total_cost_value"], t["positions_without_cost"]) == (2, 8550.0, 1)
    assert t["total_market_value"] is None and t["total_unrealised_pl"] is None      # no market rate was captured
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


# ---- profit / loss per share ---------------------------------------------------------------------
def _put_book(store, positions):
    store.put("u", "GetCollateral", NOW(), {"positions": positions})
    return reads.holdings(store, "u")["data"]


def test_holdings_pl_per_share_total_and_percent_for_a_gain_and_a_loss(store):
    d = _put_book(store, [
        {"symbol": "WIN", "quantity": 10, "avg_cost": 80.0, "market_price": 100.0, "market_value": 1000.0},
        {"symbol": "LOSE", "quantity": 20, "avg_cost": 50.0, "market_price": 45.5, "market_value": 910.0},
    ])
    win, lose = d["positions"]
    assert (win["pl_per_share"], win["pl_total"], win["pl_pct"]) == (20.0, 200.0, 25.0)
    assert (lose["pl_per_share"], lose["pl_total"], lose["pl_pct"]) == (-4.5, -90.0, -9.0)   # a loss is negative
    assert d["totals"]["total_unrealised_pl"] == 110.0
    assert d["totals"]["total_market_value"] == 1910.0 and d["totals"]["positions_without_market_price"] == 0


def test_holdings_missing_market_price_gives_none_not_zero(store):
    d = _put_book(store, [{"symbol": "X", "quantity": 10, "avg_cost": 80.0}])          # e.g. an older snapshot
    p = d["positions"][0]
    assert p["market_price"] is None and p["pl_per_share"] is None and p["pl_total"] is None and p["pl_pct"] is None
    assert d["totals"]["total_unrealised_pl"] is None and d["totals"]["total_market_value"] is None
    assert d["totals"]["positions_without_market_price"] == 1


def test_holdings_missing_cost_gives_no_pl_but_still_shows_market_value(store):
    d = _put_book(store, [{"symbol": "X", "quantity": 10, "avg_cost": None, "market_price": 100.0}])
    p = d["positions"][0]
    assert p["pl_per_share"] is None and p["market_value"] == 1000.0     # derived from qty x rate


def test_holdings_totals_sum_only_positions_that_have_a_number(store):
    d = _put_book(store, [
        {"symbol": "A", "quantity": 10, "avg_cost": 10.0, "market_price": 12.0},
        {"symbol": "B", "quantity": 10, "avg_cost": 10.0},                               # no mark
    ])
    assert d["totals"]["total_unrealised_pl"] == 20.0 and d["totals"]["positions_without_market_price"] == 1
