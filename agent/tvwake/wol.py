"""Wake-on-LAN magic packet."""

from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)


def magic_packet(mac: str) -> bytes:
    """6 x 0xFF followed by the MAC repeated 16 times."""
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(raw) != 6:
        raise ValueError(f"bad MAC {mac!r}")
    return b"\xff" * 6 + raw * 16


def send_magic_packet(mac: str, broadcast: str, ports: tuple[int, ...]) -> None:
    """Send one magic packet to each port. Raises OSError if the network refuses all of them."""
    packet = magic_packet(mac)
    errors = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in ports:
            try:
                s.sendto(packet, (broadcast, port))
            except OSError as e:  # e.g. network unreachable
                errors.append(f"port {port}: {e}")
    if len(errors) == len(ports):
        raise OSError("; ".join(errors))
    for err in errors:
        log.warning("WoL send failed on %s", err)
