"""Run the agent: python -m tvwake --env /path/to/.env"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .api import create_app
from .clock import Clock
from .config import ConfigError, load_from_file
from .mock import MockPC
from .probes import RealProbes
from . import notify
from .wake import WakeManager


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tvwake", description="tv-wake-launcher agent")
    p.add_argument("--env", help="path to the .env file")
    args = p.parse_args(argv)
    try:
        cfg = load_from_file(args.env)
    except (ConfigError, OSError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    logging.basicConfig(level=cfg.log_level, format="%(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("tvwake")

    clock = Clock()
    if cfg.mock_pc:
        probes = MockPC(clock, cfg.mock_boot_s, cfg.mock_stream_s, cfg.mock_fail,
                        cfg.mock_initially_on, cfg.mock_auto_off_s)
        log.warning("MOCK MODE: no real PC is contacted (boot %ss, server %ss, fail=%s)",
                    cfg.mock_boot_s, cfg.mock_stream_s, cfg.mock_fail or "none")
    else:
        probes = RealProbes(cfg.pc_mac, cfg.wol_broadcast, cfg.wol_ports)

    notifier = None
    if notify.enabled(cfg) and not cfg.mock_pc:
        notifier = lambda text: notify.send(cfg, text)  # noqa: E731
    manager = WakeManager(cfg, probes, clock, notifier)
    app = create_app(cfg, manager)
    log.info("listening on %s:%s (backend %s, port %s)", cfg.helper_bind, cfg.helper_port,
             cfg.stream_backend, cfg.stream_port)
    # log_config=None keeps our logging format (journald adds timestamps).
    uvicorn.run(app, host=cfg.helper_bind, port=cfg.helper_port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
