from __future__ import annotations

import asyncio
from collections.abc import Callable


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._callbacks: list[Callable[[], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def add_callback(self, callback: Callable[[], None]) -> None:
        if self.cancelled:
            callback()
            return
        self._callbacks.append(callback)

    def cancel(self) -> None:
        if self.cancelled:
            return
        self._event.set()
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback()
