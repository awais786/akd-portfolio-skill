"""`python -m akd` / `akd-app`: run the server on AKD_HOST:AKD_PORT."""
from __future__ import annotations

import logging

import uvicorn

from . import config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if config.HOST not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger("akd").warning(
            "binding to %s: this app has NO login and serves your brokerage data to anyone who can reach it",
            config.HOST,
        )
    uvicorn.run("akd.api:app", host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
