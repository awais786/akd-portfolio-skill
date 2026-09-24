"""Typed AKD records. Every one carries the time it was captured.

Without as_of, a position is a claim with no expiry - and an agent reasoning
about a three-hour-old book as if it were live is the failure mode this whole
package exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AkdPosition:
    symbol: str
    quantity: int
    avg_cost: float | None
    as_of: datetime


# No AkdCash here. The spec listed one, but nothing decodes TradeCast's
# GetAccountStatement yet (see psx_tools.tools.akd's psx_akd_cash: the tool is
# wired end-to-end and reports no_snapshot until that decoder exists), so a
# cash record would be a type with no producer and no consumer - guessing the
# field names of a ledger nobody has parsed. It goes in with the decoder.


@dataclass(frozen=True, slots=True)
class AkdOrder:
    order_id: str
    symbol: str
    side: str
    quantity: int
    price: float | None
    status: str
    placed_at: datetime
