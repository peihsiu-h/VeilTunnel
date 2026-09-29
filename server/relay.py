"""Async TCP/UDP relay — connects to target and shuffles data back."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

log = logging.getLogger(__name__)


class TcpRelay:
    """Manages one outbound TCP connection for a single stream."""

    def __init__(
        self,
        stream_id: int,
        send_cb: Callable[[int, bytes], Awaitable[None]],
        close_cb: Callable[[int], Awaitable[None]],
    ):
        self.stream_id = stream_id
        self._send_cb = send_cb
        self._close_cb = close_cb
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self, host: str, port: int, timeout: float = 10) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            self._writer = writer
            asyncio.create_task(self._read_loop(reader))
            return True
        except Exception as exc:
            log.warning("stream %d connect %s:%d failed: %s", self.stream_id, host, port, exc)
            return False

    async def send(self, data: bytes) -> None:
        if self._writer and not self._writer.is_closing():
            self._writer.write(data)
            await self._writer.drain()

    async def close(self) -> None:
        if self._writer and not self._writer.is_closing():
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await self._send_cb(self.stream_id, data)
        except Exception as exc:
            log.debug("stream %d read error: %s", self.stream_id, exc)
        finally:
            await self._close_cb(self.stream_id)


class UdpRelay(asyncio.DatagramProtocol):
    """A simple UDP relay for one stream's packet."""

    def __init__(
        self,
        stream_id: int,
        send_cb: Callable[[int, str, int, bytes], Awaitable[None]],
    ):
        self.stream_id = stream_id
        self._send_cb = send_cb
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        host, port = addr
        asyncio.ensure_future(self._send_cb(self.stream_id, host, port, data))

    def error_received(self, exc: Exception) -> None:
        log.debug("udp stream %d error: %s", self.stream_id, exc)

    async def send_to(self, host: str, port: int, data: bytes) -> None:
        if self.transport and not self.transport.is_closing():
            self.transport.sendto(data, (host, port))

    def close(self) -> None:
        if self.transport and not self.transport.is_closing():
            self.transport.close()
