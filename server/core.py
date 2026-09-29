"""Server runtime: settings, uvicorn setup and a start/stop-able runner."""

from __future__ import annotations

import logging
import os
import secrets
import threading

import uvicorn

from common.settings import data_path
from server.app import create_app
from server.banlist import BanList
from server.certs import generate_self_signed_cert

log = logging.getLogger("server")

CONFIG_FILE = data_path("server_config.json")
BAN_FILE = data_path("banned_ips.txt")
LOG_FILE = data_path("logs", "server.log")
DEFAULT_CERT = data_path("certs", "server.crt")
DEFAULT_KEY = data_path("certs", "server.key")

DEFAULTS: dict = {
    "host": "0.0.0.0",
    "port": 443,
    "psk": "",
    "tls": True,
    "cert": "",   # blank = certs/server.crt, auto-generated self-signed
    "key": "",
    "auto_ban": True,
    "language": "auto",
    "log_level": "INFO",
}

MIN_PSK_LEN = 8


def generate_psk() -> str:
    return secrets.token_urlsafe(24)


def validate(cfg: dict) -> dict:
    """Return a normalised copy of *cfg*; raise ValueError with a readable message."""
    out = dict(DEFAULTS)
    out.update(cfg)
    out["psk"] = str(out["psk"])
    if len(out["psk"]) < MIN_PSK_LEN:
        raise ValueError(f"PSK must be at least {MIN_PSK_LEN} characters")
    try:
        out["port"] = int(out["port"])
    except (TypeError, ValueError):
        raise ValueError("port must be a number") from None
    if not 1 <= out["port"] <= 65535:
        raise ValueError("port must be 1-65535")
    out["host"] = str(out["host"]).strip() or "0.0.0.0"
    out["tls"] = bool(out["tls"])
    out["auto_ban"] = bool(out["auto_ban"])
    out["cert"] = str(out["cert"] or "").strip()
    out["key"] = str(out["key"] or "").strip()
    if out["tls"] and bool(out["cert"]) != bool(out["key"]):
        raise ValueError("set both certificate and key, or leave both blank (self-signed)")
    if out["tls"] and out["cert"]:
        for path in (out["cert"], out["key"]):
            if not os.path.isfile(path):
                raise ValueError(f"file not found: {path}")
    return out


def cert_paths(cfg: dict) -> tuple[str, str]:
    return (cfg["cert"] or DEFAULT_CERT, cfg["key"] or DEFAULT_KEY)


def build_server(cfg: dict) -> uvicorn.Server:
    """Create (but don't start) the uvicorn server for a validated config."""
    ssl_kwargs: dict = {}
    if cfg["tls"]:
        cert, key = cert_paths(cfg)
        if not cfg["cert"]:
            generate_self_signed_cert(cert, key)
        ssl_kwargs = {"ssl_certfile": cert, "ssl_keyfile": key}

    banlist = BanList(BAN_FILE)
    log.info("Ban list: %s (auto-ban %s)", BAN_FILE, "on" if cfg["auto_ban"] else "off")
    log.info("Starting server on %s:%d (TLS=%s)", cfg["host"], cfg["port"], cfg["tls"])

    config = uvicorn.Config(
        create_app(cfg["psk"], banlist, cfg["auto_ban"]),
        host=cfg["host"],
        port=cfg["port"],
        # Pinned implementations: predictable in a PyInstaller bundle.
        loop="asyncio",
        http="h11",
        ws="websockets",
        # Our own logging setup (a --windowed exe has no stdout for uvicorn's).
        log_config=None,
        access_log=False,
        server_header=False,
        timeout_graceful_shutdown=3,
        **ssl_kwargs,
    )
    return uvicorn.Server(config)


class ServerRunner:
    """Runs uvicorn in a background thread; start/stop from the GUI thread."""

    def __init__(self):
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def started(self) -> bool:
        return self.running and self._server is not None and self._server.started

    def start(self, cfg: dict) -> None:
        """Build and launch the server (raises on config / certificate errors)."""
        if self.running:
            raise RuntimeError("server is already running")
        self.error = None
        self._server = build_server(cfg)
        self._thread = threading.Thread(target=self._run, name="veiltunnel-server", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        server = self._server
        assert server is not None
        try:
            server.run()
        except SystemExit:  # uvicorn exits on bind errors (port in use / denied)
            self.error = "failed to start (port already in use or access denied?)"
        except BaseException as exc:
            log.exception("server crashed")
            self.error = str(exc) or type(exc).__name__
        else:
            if not server.started and not server.should_exit:
                self.error = "failed to start — see log"

    def request_stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True

    def stop(self, timeout: float = 10) -> None:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout)
