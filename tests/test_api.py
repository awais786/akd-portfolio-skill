from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from akd import config
from akd.api import create_app
from akd.manager import AkdUnreachable, LoginRejected


@pytest.fixture
def client(store):
    with TestClient(create_app(store)) as c:
        yield c


def test_index_and_health(client):
    assert client.get("/api/health").json() == {"ok": True}
    assert "AKD App" in client.get("/").text


def test_empty_install_reports_no_snapshot_everywhere(client):
    for path in ("holdings", "cash", "orders"):
        body = client.get(f"/api/akd/{path}").json()
        assert "no_snapshot" in body["flags"]
    status = client.get("/api/akd/status").json()["data"]
    assert status["daemon"]["state"] == "stopped" and status["broker_connected"] is False


def test_holdings_round_trip(client, store):
    store.put(config.ACCOUNT_REF, "GetCollateral", datetime.now(UTC),
              {"positions": [{"symbol": "OGDC", "quantity": 10, "avg_cost": 2.0}]})
    data = client.get("/api/akd/holdings").json()["data"]
    assert data["positions"][0]["cost_value"] == 20.0 and data["broker_connected"] is True


def test_connect_maps_errors(client, monkeypatch):
    from akd import api

    async def rejected(self, u, p):
        raise LoginRejected("nope")

    async def unreachable(self, u, p):
        raise AkdUnreachable("down")

    body = {"user_id": "X", "password": "y"}
    monkeypatch.setattr(api.AkdManager, "connect", rejected)
    assert client.post("/api/akd/connect", json=body).status_code == 401
    monkeypatch.setattr(api.AkdManager, "connect", unreachable)
    assert client.post("/api/akd/connect", json=body).status_code == 502


def test_connect_requires_both_fields(client):
    assert client.post("/api/akd/connect", json={"user_id": "X"}).status_code == 422


def test_import_previews_do_not_store(client, store):
    csv = "Symbol,Qty,Avg Cost\nOGDC,100,85.5\n"
    out = client.post("/api/import/positions", json={"content": csv}).json()
    assert out["data"]["positions"][0]["symbol"] == "OGDC"
    assert store.operations(config.ACCOUNT_REF) == []
    rec = "MUGHAL;532;86.65;46179.99;BUY;T+1REG;Jun 15, 2026;Jun 16, 2026;"
    fills = client.post("/api/import/orders", json={"content": rec}).json()["data"]
    assert fills["count"] == 1 and fills["fills"][0]["side"] == "BUY"


def test_password_is_never_in_any_response(client, monkeypatch):
    from akd import api

    async def ok(self, u, p):
        return {"connected": True, "account": u}

    monkeypatch.setattr(api.AkdManager, "connect", ok)
    r = client.post("/api/akd/connect", json={"user_id": "X", "password": "s3cret-pw"})
    assert "s3cret-pw" not in r.text
