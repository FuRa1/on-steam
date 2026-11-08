"""How the agent looks at the PC: ping, TCP port check, and sending WoL.

`Probes` is the interface the wake flow uses; `RealProbes` talks to the network,
`mock.MockPC` simulates a PC. Tests use their own fakes.

Ping uses the system `ping` binary. On Ubuntu it has the cap_net_raw capability, so it
works for an unprivileged user; a raw-socket ping from Python would need root, and
unprivileged ICMP sockets are disabled by default (net.ipv4.ping_group_range = "1 0").
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Protocol

from . import wol

log = logging.getLogger(__name__)


class Probes(Protocol):
    async def ping(self, ip: str) -> bool: ...

    async def tcp_open(self, ip: str, port: int) -> bool: ...

    async def send_wol(self) -> None: ...


class RealProbes:
    def __init__(self, mac: str, broadcast: str, ports: tuple[int, ...], timeout_s: float = 1.5) -> None:
        self._mac = mac
        self._broadcast = broadcast
        self._ports = ports
        self._timeout = timeout_s
        self._ping_bin = shutil.which("ping") or "ping"

    async def ping(self, ip: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._ping_bin, "-c", "1", "-W", str(max(1, round(self._timeout))), ip,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            rc = await asyncio.wait_for(proc.wait(), timeout=self._timeout + 2)
            return rc == 0
        except (OSError, asyncio.TimeoutError) as e:
            log.debug("ping %s failed: %s", ip, e)
            return False

    async def tcp_open(self, ip: str, port: int) -> bool:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=self._timeout)
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=1)
        except (OSError, asyncio.TimeoutError):
            pass
        return True

    async def send_wol(self) -> None:
        await asyncio.to_thread(wol.send_magic_packet, self._mac, self._broadcast, self._ports)
