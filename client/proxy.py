"""Local mixed-port proxy (asyncio): SOCKS5 and HTTP on the same port.

Every connection goes through the tunnel (full tunnel, no split routing).

  * SOCKS5  — CONNECT only; SOCKS5h (domain names) is resolved on the server.
  * HTTP    — ``CONNECT host:port`` (HTTPS & anything else) and plain
              ``GET http://host/...`` requests. This is what the Windows
              system proxy speaks, so apps that follow "Internet Options"
              work without any per-app setup.

The protocol is detected from the first byte (0x05 = SOCKS5, else HTTP).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import struct
from urllib.parse import urlsplit

from common.protocol import Cmd, AddrType, encode_addr

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 15

SOCKS_OK = b"\x05\x00\x00\x01" + b"\x00" * 6
SOCKS_GENERAL_FAILURE = b"\x05\x01\x00\x01" + b"\x00" * 6
SOCKS_REFUSED = b"\x05\x05\x00\x01" + b"\x00" * 6
SOCKS_CMD_UNSUPPORTED = b"\x05\x07\x00\x01" + b"\x00" * 6

HTTP_ESTABLISHED = b"HTTP/1.1 200 Connection Established\r\n\r\n"

# Hop-by-hop headers dropped when forwarding a plain HTTP request.
_HOP_HEADERS = ("proxy-connection:", "proxy-authorization:", "connection:", "keep-alive:")


def _http_error(code: int, reason: str) -> bytes:
    body = f"{code} {reason}\n".encode()
    return (f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body


def _split_hostport(target: str, default_port: int) -> tuple[str, int]:
    """Parse "host:port", "[v6]:port" or "host"."""
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        port = int(rest[1:]) if rest.startswith(":") else default_port
        return host, port
    if target.count(":") == 1:
        host, port = target.rsplit(":", 1)
        return host, int(port)
    return target, default_port


class _Stream:
    """State of one proxied connection (one tunnel stream id)."""

    __slots__ = ("writer", "ready", "ok", "active", "closed", "early")

    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.ready = asyncio.Event()   # set on CONNECT_OK / CONNECT_FAIL / close
        self.ok = False
        self.active = False            # proxy reply sent; data may flow to the app
        self.closed = False            # server side closed the stream
        self.early: list[bytes] = []   # data that arrived before `active`


class ProxyServer:
    """Accepts local SOCKS5/HTTP proxy connections and forwards them through the tunnel."""

    def __init__(self, tunnel, bind_host: str = "127.0.0.1", bind_port: int = 1080,
                 rate_limiter=None):
        self.tunnel = tunnel
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.rate_limiter = rate_limiter
        self._counter = 0
        self._streams: dict[int, _Stream] = {}
        self._server: asyncio.Server | None = None

    def _next_stream_id(self) -> int | None:
        for _ in range(0xFFFF):
            self._counter = self._counter % 0xFFFF + 1  # 1..0xFFFF
            if self._counter not in self._streams:
                return self._counter
        return None

    # -- Callbacks from tunnel ------------------------------------------------

    async def on_data(self, stream_id: int, data: bytes) -> None:
        st = self._streams.get(stream_id)
        if st is None:
            return
        if not st.active:
            st.early.append(data)
            return
        if st.writer.is_closing():
            return
        if self.rate_limiter:
            await self.rate_limiter.consume(len(data))
        try:
            st.writer.write(data)
            await st.writer.drain()
        except (ConnectionError, OSError):
            pass

    async def on_close(self, stream_id: int) -> None:
        st = self._streams.pop(stream_id, None)
        if st is None:
            return
        st.closed = True
        st.ready.set()
        if st.active and not st.writer.is_closing():
            st.writer.close()

    async def on_connect_ok(self, stream_id: int) -> None:
        st = self._streams.get(stream_id)
        if st:
            st.ok = True
            st.ready.set()

    async def on_connect_fail(self, stream_id: int) -> None:
        st = self._streams.get(stream_id)
        if st:
            st.ready.set()

    def on_disconnect(self) -> None:
        """Tunnel dropped: every in-flight stream is dead, close them all."""
        if self._streams:
            log.info("tunnel lost — closing %d local connection(s)", len(self._streams))
        self.close_all()

    def close_all(self) -> None:
        streams, self._streams = self._streams, {}
        for st in streams.values():
            st.closed = True
            st.ready.set()
            if not st.writer.is_closing():
                st.writer.close()

    # -- tunnel stream helpers ------------------------------------------------

    async def _open(self, host: str, port: int, writer: asyncio.StreamWriter) -> tuple[int, _Stream] | None:
        """Ask the server to connect to host:port. Returns (sid, stream) or None."""
        if not self.tunnel.is_connected:
            log.warning("reject %s:%d — tunnel not connected", host, port)
            return None
        sid = self._next_stream_id()
        if sid is None:
            log.warning("reject %s:%d — too many streams", host, port)
            return None
        try:
            payload = encode_addr(host, port)
        except (UnicodeError, struct.error, ValueError) as exc:
            log.warning("reject %s:%d — bad address: %s", host, port, exc)
            return None

        st = _Stream(writer)
        self._streams[sid] = st
        log.info("stream %d → %s:%d", sid, host, port)
        try:
            await self.tunnel.send_frame(Cmd.TCP_CONNECT, sid, payload)
            await asyncio.wait_for(st.ready.wait(), timeout=CONNECT_TIMEOUT)
        except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
            log.debug("stream %d connect error: %s", sid, exc)

        if st.ok:
            return sid, st
        if self._streams.get(sid) is st:
            self._streams.pop(sid, None)
            try:  # server may still open it after our timeout
                await self.tunnel.send_frame(Cmd.TCP_CLOSE, sid)
            except Exception:
                pass
        log.info("stream %d → %s:%d failed", sid, host, port)
        return None

    async def _activate(self, st: _Stream, reply: bytes) -> bool:
        """Send the proxy reply, then flush data that raced ahead of it."""
        w = st.writer
        if reply:
            w.write(reply)
        for chunk in st.early:
            w.write(chunk)
        st.early.clear()
        st.active = True
        await w.drain()
        if st.closed:  # server already finished (e.g. banner + close)
            w.close()
            return False
        return True

    async def _pump_up(self, sid: int, st: _Stream, reader: asyncio.StreamReader) -> None:
        """Local app → tunnel until either side closes."""
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                if self.rate_limiter:
                    await self.rate_limiter.consume(len(data))
                await self.tunnel.send_frame(Cmd.TCP_DATA, sid, data)
        except Exception:
            pass
        finally:
            if self._streams.get(sid) is st:
                self._streams.pop(sid, None)
                try:
                    await self.tunnel.send_frame(Cmd.TCP_CLOSE, sid)
                except Exception:
                    pass

    # -- connection entry -----------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            first = await reader.readexactly(1)
            if first == b"\x05":
                await self._handle_socks5(reader, writer)
            else:
                await self._handle_http(first, reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as exc:
            log.debug("proxy error from %s: %s", peer, exc)
        finally:
            if not writer.is_closing():
                writer.close()

    # -- SOCKS5 ---------------------------------------------------------------

    async def _handle_socks5(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # greeting (version byte already consumed)
        nmethods = (await reader.readexactly(1))[0]
        await reader.readexactly(nmethods)
        writer.write(b"\x05\x00")  # no auth required
        await writer.drain()

        ver, cmd, _, atype = struct.unpack("!BBBB", await reader.readexactly(4))
        if ver != 0x05:
            raise ValueError(f"bad SOCKS version in request: {ver}")

        if atype == AddrType.IPV4:
            host = str(ipaddress.IPv4Address(await reader.readexactly(4)))
        elif atype == AddrType.DOMAIN:
            length = (await reader.readexactly(1))[0]
            host = (await reader.readexactly(length)).decode("ascii")
        elif atype == AddrType.IPV6:
            host = str(ipaddress.IPv6Address(await reader.readexactly(16)))
        else:
            raise ValueError(f"unsupported addr type: {atype}")
        port = struct.unpack("!H", await reader.readexactly(2))[0]

        if cmd != 0x01:  # only CONNECT; UDP ASSOCIATE / BIND are not tunnelled
            writer.write(SOCKS_CMD_UNSUPPORTED)
            await writer.drain()
            return

        opened = await self._open(host, port, writer)
        if opened is None:
            writer.write(SOCKS_GENERAL_FAILURE if not self.tunnel.is_connected else SOCKS_REFUSED)
            await writer.drain()
            return
        sid, st = opened
        if await self._activate(st, SOCKS_OK):
            await self._pump_up(sid, st, reader)

    # -- HTTP -----------------------------------------------------------------

    async def _handle_http(self, first: bytes, reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter) -> None:
        try:
            head = first + await reader.readuntil(b"\r\n\r\n")
        except asyncio.LimitOverrunError:
            writer.write(_http_error(431, "Request Header Fields Too Large"))
            await writer.drain()
            return

        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ", 2)
        except ValueError:
            writer.write(_http_error(400, "Bad Request"))
            await writer.drain()
            return

        if method.upper() == "CONNECT":
            try:
                host, port = _split_hostport(target, 443)
            except ValueError:
                writer.write(_http_error(400, "Bad Request"))
                await writer.drain()
                return
            opened = await self._open(host, port, writer)
            if opened is None:
                writer.write(_http_error(502, "Bad Gateway"))
                await writer.drain()
                return
            sid, st = opened
            if await self._activate(st, HTTP_ESTABLISHED):
                await self._pump_up(sid, st, reader)
            return

        # Plain HTTP: absolute-form request line, e.g. "GET http://host/path HTTP/1.1"
        url = urlsplit(target)
        if url.scheme.lower() != "http" or not url.hostname:
            writer.write(_http_error(400, "Bad Request"))
            await writer.drain()
            return
        try:
            port = url.port or 80
        except ValueError:
            writer.write(_http_error(400, "Bad Request"))
            await writer.drain()
            return
        path = url.path or "/"
        if url.query:
            path += "?" + url.query

        # One request per connection keeps this simple and correct: the next
        # request may target another host, so ask the origin to close after it.
        headers = [h for h in lines[1:] if h and not h.lower().startswith(_HOP_HEADERS)]
        headers.append("Connection: close")
        request = f"{method} {path} {version}\r\n" + "\r\n".join(headers) + "\r\n\r\n"

        opened = await self._open(url.hostname, port, writer)
        if opened is None:
            writer.write(_http_error(502, "Bad Gateway"))
            await writer.drain()
            return
        sid, st = opened
        try:
            await self.tunnel.send_frame(Cmd.TCP_DATA, sid, request.encode("latin-1"))
        except ConnectionError:
            self._streams.pop(sid, None)
            return
        if await self._activate(st, b""):
            await self._pump_up(sid, st, reader)

    # -- Server lifecycle -----------------------------------------------------

    async def start(self) -> asyncio.Server:
        self._server = await asyncio.start_server(
            self._handle_client, self.bind_host, self.bind_port
        )
        log.info("Proxy (SOCKS5 + HTTP) listening on %s:%d", self.bind_host, self.bind_port)
        return self._server

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        self.close_all()
        if self._server:
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=3)
            except asyncio.TimeoutError:
                pass
            self._server = None
