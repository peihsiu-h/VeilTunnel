"""VeilTunnel client — command-line entry (the GUI is client/gui.py).

Settings come from the same client_config.json the GUI uses; command-line
flags override it for this run only.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client import sysproxy
from client.core import CONFIG_FILE, DEFAULTS, PROXY_BACKUP_FILE, run_client, validate
from common.settings import load_json

log = logging.getLogger("client")


def main() -> None:
    parser = argparse.ArgumentParser(description="VeilTunnel client (SOCKS5 + HTTP proxy over WSS)")
    parser.add_argument("--config", default=CONFIG_FILE,
                        help="JSON settings file (default: %(default)s)")
    # Defaults are None so we can tell whether a flag was given.
    parser.add_argument("--server", help="Server URL, e.g. wss://host:443/ws/updates")
    parser.add_argument("--psk", help="Pre-shared key (or env VEILTUNNEL_PSK)")
    parser.add_argument("--bind", help="Local proxy bind address")
    parser.add_argument("--port", type=int, help="Local proxy port")
    parser.add_argument("--no-verify", action="store_const", const=True, default=None,
                        help="Skip TLS certificate verification (self-signed server cert)")
    parser.add_argument("--rate-limit-mbps", type=float,
                        help="Throughput cap, upload+download combined (0 = unlimited)")
    parser.add_argument("--system-proxy", action=argparse.BooleanOptionalAction, default=None,
                        help="Set the Windows system proxy while running")
    parser.add_argument("--log-level", default=None)
    args = parser.parse_args()

    cfg = load_json(args.config, DEFAULTS)
    overrides = {
        "server": args.server,
        "psk": args.psk,
        "bind": args.bind,
        "port": args.port,
        "rate_limit_mbps": args.rate_limit_mbps,
        "system_proxy": args.system_proxy,
        "log_level": args.log_level,
        "verify_ssl": (not args.no_verify) if args.no_verify is not None else None,
    }
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    if not cfg.get("psk"):
        cfg["psk"] = os.getenv("VEILTUNNEL_PSK", "")

    logging.basicConfig(level=getattr(logging, str(cfg["log_level"]).upper(), logging.INFO),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        cfg = validate(cfg)
    except ValueError as exc:
        parser.error(str(exc))

    if sysproxy.supported() and sysproxy.recover(PROXY_BACKUP_FILE):
        log.info("Restored system proxy left over from a previous run")

    log.info("Local proxy (SOCKS5 + HTTP): %s:%d", cfg["bind"], cfg["port"])
    try:
        asyncio.run(run_client(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
