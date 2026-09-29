"""WebSocket tunnel client — connects to server, multiplexes streams."""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Callable, Protocol

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI

from common.auth import generate_token
from common.protocol import Cmd, make_frame, parse_frame

log = logging.getLogger(__name__)


class AuthError(Exception):
    """The server rejected us. Retrying would not help (and may get the IP banned)."""


class StreamHandler(Protocol):
    """What the tunnel calls back into (implemented by client.proxy.ProxyServer)."""

    async def on_data(self, stream_id: int, data: bytes) -> None: ...
    async def on_close(self, stream_id: int) -> None: ...
    async def on_connect_ok(self, stream_id: int) -> None: ...
    async def on_connect_fail(self, stream_id: int) -> None: ...
    def on_disconnect(self) -> None: ...


class TunnelClient:
    """Manages the WebSocket connection to the VPN server."""

    def __init__(self, server_url: str, psk: str, verify_ssl: bool = True,
                 handler: StreamHandler | None = None):
        self.server_url = server_url
        self.psk = psk
        self.verify_ssl = verify_ssl
        self.handler = handler
        self._ws: ClientConnection | None = None
        self._connected = asyncio.Event()

    async def connect(self) -> None:
        """Connect and authenticate. Raises on failure (AuthError if fatal)."""
        ssl_ctx: ssl.SSLContext | None = None
        if self.server_url.startswith("wss://"):
            ssl_ctx = ssl.create_default_context()
            if not self.verify_ssl:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        try:
            ws = await websockets.connect(
                self.server_url,
                ssl=ssl_ctx,
                max_size=2**22,  # 4 MB
                open_timeout=10,
                # Never go through the system proxy: while connected we *are*
                # the system proxy, so honouring it would loop back into us.
                proxy=None,
            )
        except InvalidStatus as exc:
            if exc.response.status_code == 403:
                raise AuthError("server refused the connection (this IP is banned)") from exc
            raise

        await ws.send(generate_token(self.psk))
        try:
            reply = await asyncio.wait_for(ws.recv(), timeout=10)
        except ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code == 4003:
                raise AuthError("authentication failed: wrong PSK, or clocks differ by "
                                "more than ~5 minutes") from exc
            raise
        if reply != "OK":
            await ws.close()
            raise AuthError(f"unexpected auth reply: {reply!r}")

        self._ws = ws
        self._connected.set()
        log.info("Tunnel connected and authenticated")

    async def send_frame(self, cmd: int, stream_id: int, payload: bytes = b"") -> None:
        """Send one frame. Raises ConnectionError when the tunnel is down."""
        ws = self._ws
        if ws is None or not self._connected.is_set():
            raise ConnectionError("tunnel not connected")
        await ws.send(make_frame(cmd, stream_id, payload))

    async def recv_loop(self) -> None:
        """Read frames from server and dispatch to the handler."""
        assert self._ws is not None
        h = self.handler
        try:
            async for msg in self._ws:
                if not isinstance(msg, bytes) or h is None:
                    continue
                try:
                    cmd, sid, payload = parse_frame(msg)
                    if cmd == Cmd.TCP_DATA:
                        await h.on_data(sid, payload)
                    elif cmd == Cmd.TCP_CLOSE:
                        await h.on_close(sid)
                    elif cmd == Cmd.CONNECT_OK:
                        await h.on_connect_ok(sid)
                    elif cmd == Cmd.CONNECT_FAIL:
                        await h.on_connect_fail(sid)
                except Exception as exc:
                    # One misbehaving stream must never take the tunnel down.
                    log.debug("frame dispatch error: %s", exc)
        except ConnectionClosed:
            log.warning("Tunnel connection closed")
        finally:
            self._connected.clear()
            if h is not None:
                h.on_disconnect()

    async def close(self) -> None:
        self._connected.clear()
        if self._ws:
            await self._ws.close()

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()


async def run_tunnel_with_reconnect(
    tunnel: TunnelClient,
    on_status: Callable[[str, str], None] | None = None,
    retry_min: float = 3.0,
    retry_max: float = 30.0,
) -> None:
    """Keep the tunnel connected with automatic reconnection (exponential backoff).

    *on_status(state, detail)* receives "connecting" / "connected" / "reconnecting".
    AuthError and InvalidURI are fatal and propagate to the caller.
    """
    def status(state: str, detail: str = "") -> None:
        if on_status:
            on_status(state, detail)

    delay = retry_min
    status("connecting", tunnel.server_url)
    while True:
        try:
            await tunnel.connect()
            status("connected", tunnel.server_url)
            delay = retry_min
            await tunnel.recv_loop()
            detail = "connection closed"
        except (asyncio.CancelledError, AuthError, InvalidURI):
            raise
        except Exception as exc:
            detail = str(exc) or type(exc).__name__
        log.warning("Tunnel down (%s) — reconnecting in %.0fs", detail, delay)
        status("reconnecting", detail)
        await asyncio.sleep(delay)
        delay = min(delay * 2, retry_max)
