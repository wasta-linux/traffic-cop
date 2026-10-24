"""traffic-copd: the Traffic Cop daemon.

Runs as root. Loads the config (writing the default first if there is
none), applies the limits, and keeps them up to date until SIGTERM or
SIGINT, when it removes every rule it added.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from trafficcop import __version__
from trafficcop.common import config, paths
from trafficcop.daemon import nft, supervisor

log = logging.getLogger("trafficcop.daemon")


def load_or_create(path: Path) -> config.Config:
    """Load the config at ``path``, writing the default there first if missing.

    Raises ``config.ConfigError`` if it's invalid.
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config.default_text(), encoding="utf-8")
        log.info("wrote the default config to %s", path)
    return config.load(path)


async def _run(cfg: config.Config) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await supervisor.run(supervisor.Supervisor(cfg), stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="traffic-copd", description="Traffic Cop daemon: per-app bandwidth limits."
    )
    parser.add_argument(
        "--config", type=Path, help=f"config file (default: {paths.get().config})"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug output")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("asyncio").setLevel(logging.INFO)
    if os.geteuid() != 0:
        log.error("must run as root")
        return 2

    path = args.config or paths.get().config
    try:
        cfg = load_or_create(path)
    except config.ConfigError as e:
        log.error("%s", e)
        return 1
    log.info(
        "traffic-copd %s: %d apps configured in %s", __version__, len(cfg.apps), path
    )

    try:
        asyncio.run(_run(cfg))
    except nft.NftError as e:
        log.error("couldn't load the nftables rules: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
