"""VeilTunnel server — command-line entry (the GUI is server/gui.py).

Settings come from the same server_config.json the GUI uses; command-line
flags override it for this run only.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.settings import load_json
from server.core import CONFIG_FILE, DEFAULTS, LOG_FILE, build_server, validate

log = logging.getLogger("server")


def main() -> None:
    parser = argparse.ArgumentParser(description="VeilTunnel server (WSS tunnel behind a decoy website)")
    parser.add_argument("--config", default=CONFIG_FILE,
                        help="JSON settings file (default: %(default)s)")
    # Defaults are None so we can tell whether a flag was given.
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--psk", help="Pre-shared key (or env VEILTUNNEL_PSK)")
    parser.add_argument("--cert", help="TLS certificate (blank = auto self-signed)")
    parser.add_argument("--key", help="TLS private key")
    parser.add_argument("--no-tls", action="store_const", const=True, default=None,
                        help="Plain ws:// (testing only, or behind a TLS reverse proxy)")
    parser.add_argument("--auto-ban", action=argparse.BooleanOptionalAction, default=None,
                        help="Permanently ban IPs that fail authentication")
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--log-file", default=LOG_FILE,
                        help="Rotating log file (empty string to disable)")
    args = parser.parse_args()

    cfg = load_json(args.config, DEFAULTS)
    overrides = {
        "host": args.host,
        "port": args.port,
        "psk": args.psk,
        "cert": args.cert,
        "key": args.key,
        "auto_ban": args.auto_ban,
        "log_level": args.log_level,
        "tls": False if args.no_tls else None,
    }
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    if not cfg.get("psk"):
        cfg["psk"] = os.getenv("VEILTUNNEL_PSK", "")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        os.makedirs(os.path.dirname(args.log_file) or ".", exist_ok=True)
        handlers.append(RotatingFileHandler(
            args.log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    logging.basicConfig(level=getattr(logging, str(cfg["log_level"]).upper(), logging.INFO),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=handlers)

    try:
        cfg = validate(cfg)
    except ValueError as exc:
        parser.error(str(exc))

    build_server(cfg).run()


if __name__ == "__main__":
    main()
