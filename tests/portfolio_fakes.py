"""Shared fake AKD HTTP + canned bodies for the portfolio tests."""
from __future__ import annotations

import httpx  # noqa: F401

ACCOUNT = "COAF00000"
SECRET = "s3cret-pw-9"
VALID_AUTH = "<return>true|TOK-SESSION|Trader|</return>"
REJECTED_AUTH = "<return>false|Invalid password|</return>"
COLLATERAL = "<return>Symbol;Quantity;TotalQty;Avg. BUY RATE|OGDC;100;100;85.5|</return>"
GARBAGE_COLLATERAL = "<return>this is not a collateral table at all</return>"
EXPOSURE = (
    "<return>~Cash;Pending;Exposure;Outstanding Worth;Profit/Loss;Loan;Share Worth;Equity;Free Cash|"
    "1000;0;500;0;10;0;2000;3000;1500|</return>"
)
NO_ORDERS = "<return>No Record</return>"


class FakeHTTP:
    """Routes on the operation named in the request body; records every POST."""

    def __init__(self, *, auth=VALID_AUTH, collateral=COLLATERAL, exposure=EXPOSURE,
                 orders=NO_ORDERS, error: Exception | None = None):
        self.auth, self.collateral, self.exposure, self.orders = auth, collateral, exposure, orders
        self.error = error
        self.requests: list[str] = []
        self.closed = False

    async def post(self, url, content=None, headers=None):
        self.requests.append(content)
        if self.error is not None:
            raise self.error
        if "SecurecustomAuthentication" in content:
            text = self.auth
        elif "GetCollateral" in content:
            text = self.collateral
        elif "GetExposureDynamic" in content:
            text = self.exposure
        elif "GetOrderHistory" in content:
            text = self.orders
        else:  # pragma: no cover
            text = "<return></return>"

        class R:
            pass

        r = R()
        r.text = text
        r.status_code = 200
        return r

    async def aclose(self):
        self.closed = True
