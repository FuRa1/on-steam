"""Mock PC (MOCK_PC=1): lets you test the TV app without touching the real PC.

After a WoL packet the fake PC answers ping after MOCK_BOOT_S seconds and opens the
streaming port MOCK_STREAM_S seconds later. MOCK_FAIL=boot|stream makes that step never
happen, so you can see the failure screens. MOCK_INITIALLY_ON=1 starts with the PC up.
"""

from __future__ import annotations

import logging

from .clock import Clock

log = logging.getLogger(__name__)


class MockPC:
    def __init__(
        self,
        clock: Clock,
        boot_s: float,
        stream_s: float,
        fail: str = "",
        initially_on: bool = False,
        auto_off_s: float = 0,
    ) -> None:
        self._clock = clock
        self._boot_s = boot_s
        self._stream_s = stream_s
        self._fail = fail
        self._auto_off_s = auto_off_s  # 0 = stay on forever
        self._boot_at: float | None = None
        self._stream_at: float | None = None
        if initially_on:
            self._boot_at = self._stream_at = clock.monotonic()

    def _tick(self) -> float:
        """Current time; also 'powers off' the PC after auto_off_s so tests can be repeated."""
        now = self._clock.monotonic()
        if self._auto_off_s and self._boot_at is not None and now >= self._boot_at + self._auto_off_s:
            log.info("mock: PC powered itself off (MOCK_AUTO_OFF_S)")
            self.power_off()
        return now

    def _is_up(self) -> bool:
        now = self._tick()
        return self._boot_at is not None and now >= self._boot_at

    async def ping(self, ip: str) -> bool:
        return self._is_up()

    async def tcp_open(self, ip: str, port: int) -> bool:
        now = self._tick()
        return self._stream_at is not None and now >= self._stream_at

    async def send_wol(self) -> None:
        now = self._tick()
        if self._boot_at is not None:
            return  # already booting or up
        log.info("mock: WoL received, PC will boot in %.0fs", self._boot_s)
        if self._fail == "boot":
            return
        self._boot_at = now + self._boot_s
        if self._fail != "stream":
            self._stream_at = self._boot_at + self._stream_s

    def power_off(self) -> None:
        """Used by tests (and could be wired to an endpoint) to reset the fake PC."""
        self._boot_at = self._stream_at = None
