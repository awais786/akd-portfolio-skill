"""Turn stored snapshots into honest API answers.

The rule that shapes everything here: *absence is never emptiness*. When
nothing was ever captured, the field is `null` (never `[]`/`{}`/`0`) with a
`no_snapshot` flag: an empty list would claim "you own nothing". A payload that
cannot be read as the expected shape gets `payload_unreadable` and forces
`broker_connected` false. A snapshot that decoded to zero rows is a different,
legitimate fact and carries `empty_book`.

Freshness comes from `last_confirmed_at`, not `captured_at`; see store.py.

Every function returns the same envelope:
    {"data": {...}, "flags": [...], "retrieved_at": iso|None, "staleness_seconds": float|None}
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from . import config
from .store import SnapshotStore

LIVE_WINDOW = timedelta(minutes=config.LIVE_WINDOW_MINUTES)
CLOCK_SKEW_TOLERANCE = timedelta(minutes=config.CLOCK_SKEW_TOLERANCE_MINUTES)


def _envelope(data: dict, flags: list[str], retrieved_at: datetime | None) -> dict:
    staleness = None
    if retrieved_at is not None:
        when = retrieved_at if retrieved_at.tzinfo else retrieved_at.replace(tzinfo=UTC)
        staleness = max(0.0, (datetime.now(UTC) - when).total_seconds())
    return {
        "data": data,
        "flags": flags,
        "retrieved_at": retrieved_at.isoformat() if retrieved_at else None,
        "staleness_seconds": staleness,
    }


def freshness(last_confirmed_at: datetime) -> tuple[bool, list[str]]:
    """Is the CONNECTION live? A future-stamped snapshot is never live (`clock_skew`)."""
    when = last_confirmed_at if last_confirmed_at.tzinfo else last_confirmed_at.replace(tzinfo=UTC)
    age = datetime.now(UTC) - when
    if age < -CLOCK_SKEW_TOLERANCE:
        return False, ["clock_skew"]
    live = age <= LIVE_WINDOW
    return live, ([] if live else ["stale_snapshot"])


def _no_snapshot(field: str) -> dict:
    return _envelope(
        {"broker_connected": False, "as_of": None, "last_confirmed_at": None, field: None},
        ["no_snapshot"],
        None,
    )


def _rows(payload: dict, key: str) -> tuple[list | None, list[str]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return None, ["payload_unreadable"]
    return value, ([] if value else ["empty_book"])


def holdings(store: SnapshotStore, account_ref: str) -> dict:
    snap = store.latest(account_ref, "GetCollateral")
    if snap is None:
        return _no_snapshot("positions")
    positions, shape = _rows(snap["payload"], "positions")
    live, fresh = freshness(snap["last_confirmed_at"])
    unreadable = "payload_unreadable" in shape
    totals = None
    if positions:
        # Cost basis only. AKD's snapshot carries no usable current price, and
        # this app deliberately does not mark to market (see docs/ARCHITECTURE.md).
        rows = []
        for p in positions:
            cost = p.get("avg_cost")
            rows.append({**p, "cost_value": round(p["quantity"] * cost, 2) if cost is not None else None})
        positions = rows
        priced = [r["cost_value"] for r in rows if r["cost_value"] is not None]
        totals = {
            "position_count": len(rows),
            "total_cost_value": round(sum(priced), 2),
            "positions_without_cost": len(rows) - len(priced),
        }
    return _envelope(
        {
            "broker_connected": live and not unreadable,
            "positions": positions,
            "totals": totals,
            "as_of": snap["captured_at"].isoformat(),
            "last_confirmed_at": snap["last_confirmed_at"].isoformat(),
        },
        [*fresh, *shape],
        snap["last_confirmed_at"],
    )


def orders(store: SnapshotStore, account_ref: str) -> dict:
    snap = store.latest(account_ref, "GetOrderHistory")
    if snap is None:
        return _no_snapshot("orders")
    rows, shape = _rows(snap["payload"], "orders")
    live, fresh = freshness(snap["last_confirmed_at"])
    unreadable = "payload_unreadable" in shape
    return _envelope(
        {
            "broker_connected": live and not unreadable,
            "orders": rows,
            "as_of": snap["captured_at"].isoformat(),
            "last_confirmed_at": snap["last_confirmed_at"].isoformat(),
        },
        # The order-history decoder has never been checked against the broker's
        # own record of fills. Provisional until it has.
        [*fresh, *shape, "decoder_unverified"],
        snap["last_confirmed_at"],
    )


def cash(store: SnapshotStore, account_ref: str) -> dict:
    """Money state from GetExposureDynamic: cash, exposure, equity, free cash (buying power)."""
    snap = store.latest(account_ref, "GetExposureDynamic")
    if snap is None:
        return _no_snapshot("cash")
    value = snap["payload"]
    unreadable = not isinstance(value, dict) or not value
    live, fresh = freshness(snap["last_confirmed_at"])
    flags = [*fresh] + (["payload_unreadable"] if unreadable else [])
    return _envelope(
        {
            "broker_connected": live and not unreadable,
            "cash": None if unreadable else value,
            "as_of": snap["captured_at"].isoformat(),
            "last_confirmed_at": snap["last_confirmed_at"].isoformat(),
        },
        flags,
        snap["last_confirmed_at"],
    )


def status(store: SnapshotStore, account_ref: str, daemon_status: dict) -> dict:
    """What has been observed so far, plus the daemon's own state."""
    seen: dict[str, dict] = {}
    latest: datetime | None = None
    for op in store.operations(account_ref):
        snap = store.latest(account_ref, op)
        if snap is None:
            continue
        seen[op] = {
            "last_seen": snap["last_confirmed_at"].isoformat(),
            "last_changed": snap["captured_at"].isoformat(),
            "snapshot_count": store.count(account_ref, op),
        }
        if latest is None or snap["last_confirmed_at"] > latest:
            latest = snap["last_confirmed_at"]
    return _envelope(
        {
            "account_ref": account_ref,
            "operations_seen": seen,
            "daemon": daemon_status,
            # Fresh data alone is not "connected": after a disconnect the last
            # snapshots stay fresh for 15 minutes, but nothing is confirming them.
            "broker_connected": bool(latest) and freshness(latest)[0] and daemon_status.get("state") == "running",
            "as_of": latest.isoformat() if latest else None,
        },
        [] if seen else ["no_snapshot"],
        latest,
    )
