"""Switch the LG TV off over LG's control protocol (SSAP, wss://TV:3001).

The Developer Mode SSH user may not call the power service on webOS 26 ("Denied method
call"), so this uses the same protocol as the LG ThinQ app / Home Assistant, via the
aiowebostv library. The first run shows a pairing prompt on the TV that must be accepted
with the remote; the resulting client key is stored in STATE_DIR/tv_client_key (mode 600).

Usage: python -m tvwake.tvpower --env ../.env off|pair
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import ConfigError, load_from_file
from .devmode import write_private


async def _run(cfg, action: str) -> int:
    try:
        from aiowebostv import WebOsClient
    except ImportError:
        print("aiowebostv is not installed: agent/.venv/bin/pip install -r agent/requirements-tv.txt", file=sys.stderr)
        return 2

    key_file = cfg.state_dir / "tv_client_key"
    key = key_file.read_text().strip() if key_file.exists() else None
    if key is None:
        print("Pairing: accept the prompt on the TV with the remote (60 s)…")
    client = WebOsClient(cfg.tv_ip, client_key=key)
    try:
        await asyncio.wait_for(client.connect(), timeout=75)
    except (asyncio.TimeoutError, OSError) as e:
        print(f"could not connect to the TV at {cfg.tv_ip}: {e!r} (is it on? was the prompt accepted?)", file=sys.stderr)
        return 1
    try:
        if client.client_key and client.client_key != key:
            write_private(key_file, client.client_key + "\n")
            print(f"paired; key saved to {key_file}")
        if action == "off":
            await client.power_off()
            print("TV switched off")
    finally:
        await client.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tvwake.tvpower")
    p.add_argument("--env")
    p.add_argument("action", choices=["off", "pair"])
    args = p.parse_args(argv)
    try:
        cfg = load_from_file(args.env)
    except (ConfigError, OSError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    if not cfg.tv_ip:
        print("TV_IP is not set", file=sys.stderr)
        return 2
    return asyncio.run(_run(cfg, args.action))


if __name__ == "__main__":
    sys.exit(main())
