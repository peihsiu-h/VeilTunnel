"""
Tunnel binary framing protocol.

Frame format over WebSocket (binary message):
    [1 byte: cmd] [2 bytes: stream_id (big-endian)] [payload ...]

Address encoding (used in TCP_CONNECT / UDP_PACKET):
    [1 byte: addr_type] [addr] [2 bytes: port (big-endian)]
    addr_type 0x01 -> 4 bytes IPv4
    addr_type 0x03 -> 1 byte len + domain bytes
    addr_type 0x04 -> 16 bytes IPv6
"""

from __future__ import annotations

import struct
from enum import IntEnum

# --- Commands ----------------------------------------------------------------

class Cmd(IntEnum):
    TCP_CONNECT  = 0x01
    TCP_DATA     = 0x02
    TCP_CLOSE    = 0x03
    UDP_PACKET   = 0x04
    CONNECT_OK   = 0x05
    CONNECT_FAIL = 0x06

# --- Address types (same as SOCKS5) ------------------------------------------

class AddrType(IntEnum):
    IPV4   = 0x01
    DOMAIN = 0x03
    IPV6   = 0x04

# --- Frame helpers -----------------------------------------------------------

def make_frame(cmd: int, stream_id: int, payload: bytes = b"") -> bytes:
    return struct.pack("!BH", cmd, stream_id) + payload


def parse_frame(data: bytes) -> tuple[int, int, bytes]:
    """Return (cmd, stream_id, payload)."""
    if len(data) < 3:
        raise ValueError("frame too short")
    cmd, stream_id = struct.unpack("!BH", data[:3])
    return cmd, stream_id, data[3:]

# --- Address encoding --------------------------------------------------------

def encode_addr(host: str, port: int) -> bytes:
    """Encode host+port into wire format."""
    try:
        import ipaddress
        addr = ipaddress.ip_address(host)
        if addr.version == 4:
            return struct.pack("!B4sH", AddrType.IPV4, addr.packed, port)
        else:
            return struct.pack("!B16sH", AddrType.IPV6, addr.packed, port)
    except ValueError:
        # domain name
        raw = host.encode("ascii")
        return struct.pack("!BB", AddrType.DOMAIN, len(raw)) + raw + struct.pack("!H", port)


def decode_addr(data: bytes) -> tuple[str, int, int]:
    """Return (host, port, bytes_consumed)."""
    atype = data[0]
    if atype == AddrType.IPV4:
        host = ".".join(str(b) for b in data[1:5])
        port = struct.unpack("!H", data[5:7])[0]
        return host, port, 7
    elif atype == AddrType.IPV6:
        import ipaddress
        host = str(ipaddress.IPv6Address(data[1:17]))
        port = struct.unpack("!H", data[17:19])[0]
        return host, port, 19
    elif atype == AddrType.DOMAIN:
        length = data[1]
        host = data[2:2 + length].decode("ascii")
        port = struct.unpack("!H", data[2 + length:4 + length])[0]
        return host, port, 4 + length
    else:
        raise ValueError(f"unknown addr type: {atype:#x}")
