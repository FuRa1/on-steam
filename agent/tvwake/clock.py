"""Injectable clock so the wake state machine can be tested without real waiting."""

from __future__ import annotations

import asyncio
import time


class Clock:
    """Real clock. `monotonic()` for measuring durations, `wall()` for timestamps shown to humans."""

    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> float:
        return time.time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeClock(Clock):
    """Virtual time: sleep() advances the clock instantly. Single-task use (tests, mock mode tests)."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self._t = 0.0
        self._wall0 = start

    def monotonic(self) -> float:
        return self._t

    def wall(self) -> float:
        return self._wall0 + self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds

    async def sleep(self, seconds: float) -> None:
        self._t += max(0.0, seconds)
        await asyncio.sleep(0)  # still yield to the event loop
