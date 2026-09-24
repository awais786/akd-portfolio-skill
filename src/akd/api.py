"""HTTP API + the single-page UI. Local, single-user, no authentication."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, reads
from .manager import AkdManager, AkdUnreachable, LoginRejected
from .parse import parse as parse_positions
from .parse import parse_orderhistory
from .store import SnapshotStore

log = logging.getLogger("akd.api")
STATIC = Path(__file__).parent / "static"


class ConnectBody(BaseModel):
    user_id: str = Field(min_length=1, description="AKD account, e.g. COAF00000")
    password: str = Field(min_length=1, description="FULL password (not the 4 challenge characters)")


class ImportBody(BaseModel):
    content: str = Field(min_length=1)


def create_app(store: SnapshotStore | None = None) -> FastAPI:
    """App factory. `store` is injectable so tests use a temp database."""
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = store or SnapshotStore()
        mgr = AkdManager(s)
        state["store"], state["mgr"] = s, mgr
        if await mgr.start_persisted():
            log.info("restored saved AKD session; daemon starting")
        yield
        await mgr.stop()
        if store is None:
            s.close()

    app = FastAPI(title="AKD App", version="0.1.0", lifespan=lifespan)

    def _s() -> SnapshotStore:
        return state["store"]

    def _m() -> AkdManager:
        return state["mgr"]

    @app.get("/api/akd/holdings")
    async def holdings() -> dict:
        return reads.holdings(_s(), config.ACCOUNT_REF)

    @app.get("/api/akd/cash")
    async def cash() -> dict:
        return reads.cash(_s(), config.ACCOUNT_REF)

    @app.get("/api/akd/orders")
    async def orders() -> dict:
        return reads.orders(_s(), config.ACCOUNT_REF)

    @app.get("/api/akd/status")
    async def status() -> dict:
        return reads.status(_s(), config.ACCOUNT_REF, _m().daemon_status())

    @app.post("/api/akd/connect")
    async def connect(body: ConnectBody) -> dict:
        try:
            return {"data": await _m().connect(body.user_id, body.password), "flags": []}
        except LoginRejected as exc:
            raise HTTPException(401, str(exc)) from exc
        except AkdUnreachable as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/akd/disconnect")
    async def disconnect() -> dict:
        await _m().disconnect()
        return {"data": {"connected": False}, "flags": []}

    # Import helpers: parse a TradeCast export you paste in. Preview only - nothing is stored.
    @app.post("/api/import/positions")
    async def import_positions(body: ImportBody) -> dict:
        result = parse_positions(body.content)
        return {"data": asdict(result), "flags": [] if result.positions else ["no_positions_found"]}

    @app.post("/api/import/orders")
    async def import_orders(body: ImportBody) -> dict:
        fills = parse_orderhistory(body.content)
        return {"data": {"fills": [asdict(f) for f in fills], "count": len(fills)},
                "flags": [] if fills else ["no_fills_found"]}

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
