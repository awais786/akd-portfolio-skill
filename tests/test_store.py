from __future__ import annotations

from datetime import UTC, datetime, timedelta

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
A = {"positions": [{"symbol": "OGDC", "quantity": 100, "avg_cost": 85.5}]}
B = {"positions": [{"symbol": "PPL", "quantity": 5, "avg_cost": 100.0}]}


def test_latest_is_none_when_nothing_captured(store):
    assert store.latest("u", "GetCollateral") is None


def test_identical_content_is_not_a_new_row_but_advances_confirmation(store):
    assert store.put("u", "GetCollateral", T0, A) is True
    assert store.put("u", "GetCollateral", T0 + timedelta(minutes=5), A) is False
    snap = store.latest("u", "GetCollateral")
    assert store.count("u", "GetCollateral") == 1
    assert snap["captured_at"] == T0
    assert snap["last_confirmed_at"] == T0 + timedelta(minutes=5)


def test_older_confirmation_never_moves_liveness_backwards(store):
    store.put("u", "GetCollateral", T0 + timedelta(minutes=5), A)
    store.put("u", "GetCollateral", T0, A)
    assert store.latest("u", "GetCollateral")["last_confirmed_at"] == T0 + timedelta(minutes=5)


def test_sell_then_rebuy_returns_the_rebought_book(store):
    """A, then B, then A again: the third put collides with A's row but must win as latest."""
    store.put("u", "GetCollateral", T0, A)
    store.put("u", "GetCollateral", T0 + timedelta(minutes=1), B)
    store.put("u", "GetCollateral", T0 + timedelta(minutes=2), A)
    assert store.latest("u", "GetCollateral")["payload"] == A


def test_key_order_does_not_read_as_a_change(store):
    store.put("u", "op", T0, {"a": 1, "b": 2})
    assert store.put("u", "op", T0, {"b": 2, "a": 1}) is False


def test_same_second_tie_prefers_the_row_written_second(store):
    store.put("u", "GetCollateral", T0, A)
    store.put("u", "GetCollateral", T0, B)
    assert store.latest("u", "GetCollateral")["payload"] == B


def test_accounts_and_operations_are_isolated(store):
    store.put("u1", "GetCollateral", T0, A)
    assert store.latest("u2", "GetCollateral") is None
    assert store.operations("u1") == ["GetCollateral"]


def test_delete_all(store):
    store.put("u", "op", T0, A)
    assert store.delete_all() == 1
    assert store.latest("u", "op") is None
