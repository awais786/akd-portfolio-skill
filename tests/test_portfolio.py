"""`fetch_portfolio`: one login, one read pass, honest envelopes. No network, no real credentials."""
from __future__ import annotations

import json

import httpx
import pytest
from portfolio_fakes import (
    ACCOUNT,
    GARBAGE_COLLATERAL,
    REJECTED_AUTH,
    SECRET,
    FakeHTTP,
)

from akd.portfolio import PortfolioError, fetch_portfolio


async def test_happy_path_returns_all_three_sections():
    result = await fetch_portfolio(ACCOUNT, SECRET, http=FakeHTTP())
    assert result["account"] == ACCOUNT
    assert result["holdings"]["data"]["positions"][0]["symbol"] == "OGDC"
    assert result["cash"]["data"]["cash"]["free_cash_pkr"] == 1500.0
    assert result["orders"]["data"]["orders"] == []
    assert "decoder_unverified" in result["orders"]["flags"]
    assert "no_snapshot" not in result["orders"]["flags"]


async def test_rejected_login_raises_after_one_request():
    http = FakeHTTP(auth=REJECTED_AUTH)
    with pytest.raises(PortfolioError) as exc:
        await fetch_portfolio(ACCOUNT, SECRET, http=http)
    assert exc.value.code == "login_rejected"
    assert len(http.requests) == 1  # no retry, no reads after a rejection


async def test_zero_row_collateral_is_no_snapshot_not_empty():
    result = await fetch_portfolio(ACCOUNT, SECRET, http=FakeHTTP(collateral=GARBAGE_COLLATERAL))
    assert "no_snapshot" in result["holdings"]["flags"]
    assert result["holdings"]["data"]["positions"] is None
    assert result["poll"]["GetCollateral"] is False


async def test_network_error_maps_to_akd_unreachable():
    http = FakeHTTP(error=httpx.ConnectError("boom"))
    with pytest.raises(PortfolioError) as exc:
        await fetch_portfolio(ACCOUNT, SECRET, http=http)
    assert exc.value.code == "akd_unreachable"


@pytest.mark.parametrize(("user", "password"), [("  ", SECRET), (ACCOUNT, ""), (ACCOUNT, "   ")])
async def test_blank_credentials_raise_before_any_request(user, password):
    http = FakeHTTP()
    with pytest.raises(PortfolioError) as exc:
        await fetch_portfolio(user, password, http=http)
    assert exc.value.code == "missing_credentials"
    assert http.requests == []


async def test_password_not_in_result_or_error_text():
    result = await fetch_portfolio(ACCOUNT, SECRET, http=FakeHTTP())
    assert SECRET not in json.dumps(result)
    with pytest.raises(PortfolioError) as exc:
        await fetch_portfolio(ACCOUNT, SECRET, http=FakeHTTP(auth=REJECTED_AUTH))
    assert SECRET not in exc.value.message


async def test_fetch_portfolio_does_not_create_the_data_dir(tmp_path, monkeypatch):
    """A one-shot fetch uses a throwaway temp store; it must not leave a ./data folder behind."""
    from akd import config

    data_dir = tmp_path / "nodata"
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", data_dir / "akd.db")
    await fetch_portfolio(ACCOUNT, SECRET, http=FakeHTTP())
    assert not data_dir.exists()
