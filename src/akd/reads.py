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


def _with_pl(p: dict) -> dict:
    """Add cost value, market value and profit/loss to one stored position.

    Every derived number is None (never 0) when an input is missing: a zero would read as
    "no gain/loss". P/L per share = AKD's market rate - your average cost; total = per share x quantity.
    """
    qty, cost, mark = p.get("quantity"), p.get("avg_cost"), p.get("market_price")
    value = p.get("market_value")
    if value is None and mark is not None and qty is not None:
        value = round(qty * mark, 2)
    pl_share = round(mark - cost, 4) if mark is not None and cost is not None else None
    pl_total = round(pl_share * qty, 2) if pl_share is not None and qty is not None else None
    pl_pct = round(pl_share / cost * 100, 2) if pl_share is not None and cost else None
    return {
        **p,
        "cost_value": round(qty * cost, 2) if cost is not None and qty is not None else None,
        "market_price": mark,
        "market_value": value,
        "pl_per_share": pl_share,
        "pl_total": pl_total,
        "pl_pct": pl_pct,
    }


def holdings(store: SnapshotStore, account_ref: str) -> dict:
    snap = store.latest(account_ref, "GetCollateral")
    if snap is None:
        return _no_snapshot("positions")
    positions, shape = _rows(snap["payload"], "positions")
    live, fresh = freshness(snap["last_confirmed_at"])
    unreadable = "payload_unreadable" in shape
    totals = None
    if positions:
        rows = [_with_pl(p) for p in positions]
        positions = rows
        cost = [r["cost_value"] for r in rows if r["cost_value"] is not None]
        marked = [r for r in rows if r["market_value"] is not None]
        pl_rows = [r["pl_total"] for r in rows if r["pl_total"] is not None]
        totals = {
            "position_count": len(rows),
            "total_cost_value": round(sum(cost), 2),
            "positions_without_cost": len(rows) - len(cost),
            # AKD's own mark (its "MTM" rate) as of the snapshot, NOT a live PSX quote.
            "total_market_value": round(sum(r["market_value"] for r in marked), 2) if marked else None,
            "total_unrealised_pl": round(sum(pl_rows), 2) if pl_rows else None,
            "positions_without_market_price": len(rows) - len(marked),
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
