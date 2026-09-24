"""Start/stop/report one long-running coroutine. A crash is recorded, never raised."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("akd.supervisor")

STOP_TIMEOUT = 5.0


class DaemonSupervisor:
    """`stop()` is bounded: a daemon that ignores cancellation is abandoned and
    logged rather than allowed to hang shutdown. `status()` keeps a crash's
    error until the next `start()`."""

    def __init__(self, name: str, run: Callable[[], Awaitable[None]]) -> None:
        self.name = name
        self._run = run
        self._task: asyncio.Task | None = None
        self._error: str | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._error = None
        self._task = asyncio.create_task(self._supervise())

    async def _supervise(self) -> None:
        try:
            await self._run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # recorded, not propagated
            self._error = f"{type(exc).__name__}: {exc}"
            log.warning("daemon %s crashed: %s", self.name, self._error, exc_info=True)

    async def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        if self._task is None:
            return
        task = self._task
        task.cancel()
        _done, pending = await asyncio.wait({task}, timeout=timeout)
        if pending:
            log.warning("daemon %s did not stop within %.1fs; abandoning it", self.name, timeout)
        else:
            with contextlib.suppress(asyncio.CancelledError):
                task.result()
        self._task = None

    def status(self) -> dict:
        if self._error is not None:
            state = "crashed"
        elif self._task is not None and not self._task.done():
            state = "running"
        else:
            state = "stopped"
        return {"name": self.name, "state": state, "error": self._error}
