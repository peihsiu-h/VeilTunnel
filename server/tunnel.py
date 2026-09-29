"""WebSocket tunnel handler — stream multiplexing + relay management."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

from common.auth import verify_token
from common.protocol import (
    Cmd, make_frame, parse_frame, encode_addr, decode_addr,
)
from server.relay import TcpRelay, UdpRelay

log = logging.getLogger(__name__)


class TunnelSession:
    """One authenticated WebSocket = one TunnelSession with many streams."""

    def __init__(self, ws: WebSocket, peer: str = "?"):
        self.ws = ws
        self.peer = peer
        self._tcp_relays: dict[int, TcpRelay] = {}
        self._udp_relays: dict[int, UdpRelay] = {}
        self._connecting: dict[int, asyncio.Task] = {}  # in-flight TCP_CONNECTs

    # -- callbacks for relay → client -----------------------------------------

    async def _on_tcp_data(self, stream_id: int, data: bytes) -> None:
        frame = make_frame(Cmd.TCP_DATA, stream_id, data)
        try:
            await self.ws.send_bytes(frame)
        except Exception:
            pass

    async def _on_tcp_close(self, stream_id: int) -> None:
        frame = make_frame(Cmd.TCP_CLOSE, stream_id)
        try:
            await self.ws.send_bytes(frame)
        except Exception:
            pass
        self._tcp_relays.pop(stream_id, None)

    async def _on_udp_data(self, stream_id: int, host: str, port: int, data: bytes) -> None:
        payload = encode_addr(host, port) + data
        frame = make_frame(Cmd.UDP_PACKET, stream_id, payload)
        try:
            await self.ws.send_bytes(frame)
        except Exception:
            pass

    # -- frame dispatch -------------------------------------------------------

    async def handle_frame(self, raw: bytes) -> None:
        cmd, sid, payload = parse_frame(raw)

        if cmd == Cmd.TCP_CONNECT:
            # Connect concurrently so one slow/unreachable host can't stall
            # every other stream multiplexed on this WebSocket.
            task = asyncio.create_task(self._handle_connect(sid, payload))
            self._connecting[sid] = task
            task.add_done_callback(lambda t, s=sid: self._connecting.pop(s, None)
                                   if self._connecting.get(s) is t else None)
        elif cmd == Cmd.TCP_DATA:
            relay = self._tcp_relays.get(sid)
            if relay:
                await relay.send(payload)
        elif cmd == Cmd.TCP_CLOSE:
            task = self._connecting.pop(sid, None)
            if task:
                task.cancel()
            relay = self._tcp_relays.pop(sid, None)
            if relay:
                await relay.close()
        elif cmd == Cmd.UDP_PACKET:
            await self._handle_udp(sid, payload)

    async def _handle_connect(self, sid: int, payload: bytes) -> None:
        try:
            host, port, _ = decode_addr(payload)
        except (ValueError, IndexError, UnicodeError):
            ok = False
        else:
            log.info("[%s] stream %d → TCP CONNECT %s:%d", self.peer, sid, host, port)
            relay = TcpRelay(sid, self._on_tcp_data, self._on_tcp_close)
            ok = await relay.connect(host, port)
            if ok:
                self._tcp_relays[sid] = relay

        frame = make_frame(Cmd.CONNECT_OK, sid) if ok else make_frame(Cmd.CONNECT_FAIL, sid, b"\x01")
        try:
            await self.ws.send_bytes(frame)
        except Exception:
            pass

    async def _handle_udp(self, sid: int, payload: bytes) -> None:
        host, port, consumed = decode_addr(payload)
        data = payload[consumed:]

        relay = self._udp_relays.get(sid)
        if relay is None:
            loop = asyncio.get_running_loop()
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: UdpRelay(sid, self._on_udp_data),
                local_addr=("0.0.0.0", 0),
            )
            relay = protocol  # type: ignore[assignment]
            self._udp_relays[sid] = relay

        await relay.send_to(host, port, data)

    # -- cleanup --------------------------------------------------------------

    async def cleanup(self) -> None:
        for task in self._connecting.values():
            task.cancel()
        self._connecting.clear()
        for relay in self._tcp_relays.values():
            await relay.close()
        for relay in self._udp_relays.values():
            relay.close()
        self._tcp_relays.clear()
        self._udp_relays.clear()


async def handle_websocket(ws: WebSocket, psk: str, banlist=None, auto_ban: bool = True) -> None:
    """Entry point called from FastAPI route."""
    ip = ws.client.host if ws.client else "unknown"
    peer = f"{ip}:{ws.client.port}" if ws.client else "unknown"

    # --- reject banned IPs at the handshake (no WebSocket established) --------
    if banlist is not None and banlist.is_banned(ip):
        log.warning("rejected banned IP %s", peer)
        try:
            await ws.close(code=4403, reason="banned")
        except Exception:
            pass
        return

    await ws.accept()
    log.info("connection from %s", peer)

    # --- authenticate --------------------------------------------------------
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=10)
    except Exception:
        log.warning("auth timeout from %s", peer)
        await ws.close(code=4001, reason="auth timeout")
        return

    if not verify_token(psk, first):
        # A valid client never mistypes a copy-pasted PSK, so a wrong token is
        # treated as malicious: log and (if enabled) permanently ban the source IP.
        if auto_ban and banlist is not None and banlist.ban(ip):
            log.warning("AUTH FAILED from %s — IP permanently banned", peer)
        else:
            log.warning("AUTH FAILED from %s", peer)
        await ws.close(code=4003, reason="forbidden")
        return

    await ws.send_text("OK")
    log.info("client authenticated: %s", peer)

    # --- main loop -----------------------------------------------------------
    session = TunnelSession(ws, peer)
    try:
        while True:
            data = await ws.receive_bytes()
            await session.handle_frame(data)
    except WebSocketDisconnect:
        log.info("client disconnected: %s", peer)
    except Exception as exc:
        log.warning("tunnel error from %s: %s", peer, exc)
    finally:
        await session.cleanup()
