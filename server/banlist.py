"""Persistent IP ban list — plain text, one IP per line, user-editable.

The file is the single source of truth: edit it by hand to add or remove bans
while the server is running (changes are picked up automatically via mtime).
Lines may have trailing '# comments'. Auto-banning never touches loopback, so
local testing can't lock you out — but a manual loopback entry is still honored.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Never *auto*-ban these (protects against self-lockout during local testing).
_NEVER_AUTO_BAN = {"127.0.0.1", "::1", "localhost"}


class BanList:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._ips: set[str] = set()
        self._mtime: float = -1.0
        self._reload_if_changed()

    def _reload_if_changed(self) -> None:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            if self._mtime != -1.0:  # file was removed -> clear
                self._ips = set()
                self._mtime = -1.0
            return
        if mtime == self._mtime:
            return
        ips: set[str] = set()
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    s = line.split("#", 1)[0].strip()
                    if s:
                        ips.add(s)
        except OSError as exc:
            log.warning("cannot read ban file %s: %s", self.path, exc)
            return
        self._ips = ips
        self._mtime = mtime

    def is_banned(self, ip: str) -> bool:
        with self._lock:
            self._reload_if_changed()
            return ip in self._ips

    def ban(self, ip: str) -> bool:
        """Auto-ban an IP. Returns True if newly banned."""
        if ip in _NEVER_AUTO_BAN:
            return False
        with self._lock:
            self._reload_if_changed()
            if ip in self._ips:
                return False
            self._ips.add(ip)
            try:
                d = os.path.dirname(self.path)
                if d:
                    os.makedirs(d, exist_ok=True)
                new_file = not os.path.exists(self.path)
                with open(self.path, "a", encoding="utf-8") as f:
                    if new_file:
                        f.write("# VeilTunnel banned IPs - one per line. "
                                "Edit freely; '#' starts a comment.\n")
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
                    f.write(f"{ip}    # auto-banned {ts}\n")
                self._mtime = os.path.getmtime(self.path)
            except OSError as exc:
                log.warning("cannot write ban file %s: %s", self.path, exc)
            return True

    def entries(self) -> list[tuple[str, str]]:
        """Return [(ip, comment)] in file order (for display)."""
        out: list[tuple[str, str]] = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    ip, _, comment = line.partition("#")
                    if ip.strip():
                        out.append((ip.strip(), comment.strip()))
        except OSError:
            pass
        return out

    def unban(self, ip: str) -> bool:
        """Remove every line for *ip*, keeping comments and other entries."""
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                return False
            kept = [ln for ln in lines if ln.split("#", 1)[0].strip() != ip]
            if len(kept) == len(lines):
                return False
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(kept)
            self._mtime = -2.0  # force reload on next check
            self._reload_if_changed()
            log.info("unbanned %s", ip)
            return True
