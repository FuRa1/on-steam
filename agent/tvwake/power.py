"""Turn the PC off on request (the "PC Off" app on the TV).

The helper already has admin ssh access to the PC (PC_SSH, an ssh host alias or user@host), so
this is one `shutdown /s /f` over ssh. Forced on purpose: the button is for "something went
wrong", so a hung game must not block it. The PC itself reports the shutdown to Telegram
(pc/events.ps1, pc.off).
"""

from __future__ import annotations

import asyncio
import logging

from .config import Config
from .probes import Probes

log = logging.getLogger(__name__)

SSH_TIMEOUT_S = 25
# Runs in the PC's default ssh shell (cmd or PowerShell: both take this as is).
COMMAND = 'shutdown /s /f /t 0 /c "PC Off on the TV"'


class PowerError(Exception):
    pass


async def pc_is_up(cfg: Config, probes: Probes) -> bool:
    """Same test as the wake flow: ping OR the streaming port. Windows may drop ping (its firewall
    blocks ICMP on a "Public" network), while Apollo still answers on its port."""
    results = await asyncio.gather(probes.ping(cfg.pc_ip), probes.tcp_open(cfg.pc_ip, cfg.stream_port))
    return any(results)


async def shutdown(cfg: Config, probes: Probes) -> str:
    """Ask the PC to shut down. Returns "off" (it was off already) or "sent"; raises PowerError."""
    if not await pc_is_up(cfg, probes):
        return "off"
    if cfg.mock_pc:
        probes.power_off()  # type: ignore[attr-defined]
        log.info("mock: PC switched off")
        return "sent"
    if not cfg.pc_ssh:
        raise PowerError("PC_SSH is not set in the helper's .env")
    log.info("shutting the PC down via ssh %s", cfg.pc_ssh)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", cfg.pc_ssh, COMMAND,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=SSH_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise PowerError("the PC did not answer over ssh") from None
    except OSError as e:
        raise PowerError(f"cannot run ssh: {e}") from None
    if proc.returncode != 0:
        lines = [l for l in err.decode(errors="replace").splitlines() if l.strip() and "post-quantum" not in l
                 and not l.startswith("**")]
        reason = lines[-1].strip() if lines else f"ssh exit code {proc.returncode}"
        log.warning("shutdown over ssh failed: %s", reason)
        raise PowerError(reason)
    return "sent"
