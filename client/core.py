"""Client runtime: tunnel + local proxy (+ optional system proxy).

``run_client()`` is the async entry used by the CLI; ``ClientRunner`` runs the
same thing on a private event loop in a worker thread for the GUI.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable

from client import sysproxy
from client.proxy import ProxyServer
from client.tunnel import TunnelClient, run_tunnel_with_reconnect
from common.ratelimit import make_rate_limiter
from common.settings import data_path

log = logging.getLogger("client")

CONFIG_FILE = data_path("client_config.json")
PROXY_BACKUP_FILE = data_path("sysproxy_backup.json")

DEFAULTS: dict = {
    "server": "wss://your.server.example:443/ws/updates",
    "psk": "",
    "verify_ssl": True,
    "bind": "127.0.0.1",
    "port": 1080,
    "rate_limit_mbps": 0,
    "system_proxy": True,
    "language": "auto",
    "log_level": "INFO",
}

StatusCallback = Callable[[str, str], None]


def validate(cfg: dict) -> dict:
    """Return a normalised copy of *cfg*; raise ValueError with a readable message."""
    out = dict(DEFAULTS)
    out.update(cfg)
    out["server"] = str(out["server"]).strip()
    if not out["server"].startswith(("ws://", "wss://")):
        raise ValueError("server URL must start with wss:// (or ws:// for testing)")
    out["psk"] = str(out["psk"])
    if not out["psk"]:
        raise ValueError("PSK is empty")
    try:
        out["port"] = int(out["port"])
    except (TypeError, ValueError):
        raise ValueError("local port must be a number") from None
    if not 1 <= out["port"] <= 65535:
        raise ValueError("local port must be 1-65535")
    try:
        out["rate_limit_mbps"] = float(out["rate_limit_mbps"] or 0)
    except (TypeError, ValueError):
        raise ValueError("rate limit must be a number (0 = unlimited)") from None
    if out["rate_limit_mbps"] < 0:
        raise ValueError("rate limit must be >= 0")
    out["bind"] = str(out["bind"]).strip() or "127.0.0.1"
    out["verify_ssl"] = bool(out["verify_ssl"])
    out["system_proxy"] = bool(out["system_proxy"])
    return out


def _quiet_exception_handler(loop, context):
    """Swallow benign ConnectionResetError noise (Windows Proactor force-close)."""
    if isinstance(context.get("exception"), ConnectionResetError):
        return
    loop.default_exception_handler(context)


async def run_client(cfg: dict, on_status: StatusCallback | None = None,
                     stop_event: asyncio.Event | None = None) -> None:
    """Run until *stop_event* is set, the task is cancelled, or a fatal error.

    *on_status(state, detail)* states: connecting / connected / reconnecting /
    error / stopped.
    """
    status: StatusCallback = on_status or (lambda s, d: None)
    asyncio.get_running_loop().set_exception_handler(_quiet_exception_handler)

    tunnel = TunnelClient(cfg["server"], cfg["psk"], cfg["verify_ssl"])
    proxy = ProxyServer(tunnel, cfg["bind"], cfg["port"],
                        rate_limiter=make_rate_limiter(cfg["rate_limit_mbps"]))
    tunnel.handler = proxy

    try:
        await proxy.start()
    except OSError as exc:
        msg = f"cannot listen on {cfg['bind']}:{cfg['port']}: {exc}"
        log.error("%s", msg)
        status("error", msg)
        return

    log.info("Server: %s (verify TLS: %s)", cfg["server"], cfg["verify_ssl"])
    log.info("Rate limit: %s", f"{cfg['rate_limit_mbps']:g} Mbps" if cfg["rate_limit_mbps"] else "unlimited")

    # Point the system proxy at us right away (not only once the tunnel is up):
    # while the tunnel is down, traffic fails instead of leaking out directly.
    snapshot = None
    if cfg["system_proxy"]:
        if sysproxy.supported():
            try:
                snapshot = sysproxy.enable(cfg["bind"], cfg["port"])
                sysproxy.save_backup(PROXY_BACKUP_FILE, cfg["bind"], cfg["port"], snapshot)
            except OSError as exc:
                log.error("cannot set system proxy: %s", exc)
        else:
            log.warning("System proxy is only supported on Windows; "
                        "configure your apps to use %s:%d", cfg["bind"], cfg["port"])

    tunnel_task = asyncio.create_task(run_tunnel_with_reconnect(tunnel, on_status=status))
    waiters: set[asyncio.Task] = {tunnel_task}
    if stop_event is not None:
        waiters.add(asyncio.create_task(stop_event.wait()))

    final = ("stopped", "")
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if tunnel_task.done() and not tunnel_task.cancelled() and tunnel_task.exception():
            exc = tunnel_task.exception()
            log.error("%s", exc)
            final = ("error", str(exc))
    finally:
        # Restore the system proxy first: it matters most if anything below hangs.
        if snapshot is not None:
            try:
                sysproxy.restore(snapshot)
                sysproxy.clear_backup(PROXY_BACKUP_FILE)
            except OSError as exc:
                log.error("cannot restore system proxy: %s", exc)
        for t in waiters:
            t.cancel()
        await proxy.stop()
        try:
            await asyncio.wait_for(tunnel.close(), timeout=3)
        except Exception:
            pass
        await asyncio.gather(*waiters, return_exceptions=True)
        log.info("Client stopped")
        status(*final)


class ClientRunner:
    """Runs run_client() in a background thread; start/stop from the GUI thread."""

    def __init__(self, on_status: StatusCallback):
        self._on_status = on_status
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, cfg: dict) -> None:
        if self.running:
            raise RuntimeError("client is already running")
        self._ready.clear()
        self._loop = self._stop = None
        self._thread = threading.Thread(target=self._thread_main, args=(cfg,),
                                        name="veiltunnel-client", daemon=True)
        self._thread.start()

    def _thread_main(self, cfg: dict) -> None:
        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            self._ready.set()
            await run_client(cfg, self._on_status, self._stop)

        try:
            asyncio.run(main())
        except Exception as exc:
            log.exception("client crashed")
            self._on_status("error", str(exc))
        finally:
            self._ready.set()

    def request_stop(self) -> None:
        """Ask the worker to stop (non-blocking)."""
        if not self.running:
            return
        self._ready.wait(2)
        loop, evt = self._loop, self._stop
        if loop is not None and evt is not None:
            try:
                loop.call_soon_threadsafe(evt.set)
            except RuntimeError:  # loop already closed
                pass

    def stop(self, timeout: float = 8) -> None:
        """Stop and wait for the worker thread to finish."""
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout)
