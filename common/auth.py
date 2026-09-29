"""Simple pre-shared key authentication."""

from __future__ import annotations

import hashlib
import hmac
import time


def generate_token(psk: str, window: int = 30) -> str:
    """Generate a time-based token from the pre-shared key.

    Token = HMAC-SHA256(psk, timestamp // window) in hex.
    Valid for *window* seconds.
    """
    ts = int(time.time()) // window
    return hmac.new(
        psk.encode(), str(ts).encode(), hashlib.sha256
    ).hexdigest()


def verify_token(psk: str, token: str, window: int = 30, drift: int = 10) -> bool:
    """Verify token, allowing ±drift windows to handle clock skew."""
    ts = int(time.time()) // window
    for offset in range(-drift, drift + 1):
        expected = hmac.new(
            psk.encode(), str(ts + offset).encode(), hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected, token):
            return True
    return False
