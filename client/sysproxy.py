"""Windows system proxy ("Internet Options" / WinINet) — set and restore.

Only affects programs that follow the system proxy (browsers, most GUI apps,
many .NET / Python programs). It does not capture raw UDP or apps that ignore
proxy settings.
"""

from __future__ import annotations

import json
import logging
import os
import sys

log = logging.getLogger(__name__)

KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
VALUES = ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL")

# LAN / link-local destinations stay direct ("all *outbound* traffic" goes via VPN).
BYPASS = ";".join(
    ["localhost", "127.*", "10.*", "192.168.*", "169.254.*"]
    + [f"172.{n}.*" for n in range(16, 32)]
    + ["<local>"]
)

_INTERNET_OPTION_SETTINGS_CHANGED = 39
_INTERNET_OPTION_REFRESH = 37


def supported() -> bool:
    return sys.platform == "win32"


def _proxy_address(host: str, port: int) -> str:
    # A wildcard bind still has to be reached through loopback.
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"{host}:{port}"


def read() -> dict:
    """Snapshot the current settings ({name: value-or-None})."""
    import winreg
    snap: dict = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
        for name in VALUES:
            try:
                snap[name] = winreg.QueryValueEx(k, name)[0]
            except FileNotFoundError:
                snap[name] = None
    return snap


def _write(values: dict) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        for name, value in values.items():
            if value is None:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
            elif name == "ProxyEnable":
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))
            else:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(value))
    _notify()


def _notify() -> None:
    """Tell running WinINet apps that the settings changed."""
    try:
        import ctypes
        inet = ctypes.windll.wininet
        inet.InternetSetOptionW(None, _INTERNET_OPTION_SETTINGS_CHANGED, None, 0)
        inet.InternetSetOptionW(None, _INTERNET_OPTION_REFRESH, None, 0)
    except Exception as exc:
        log.debug("InternetSetOption failed: %s", exc)


def enable(host: str, port: int) -> dict:
    """Point the system proxy at host:port. Returns the previous snapshot."""
    prev = read()
    _write({
        "ProxyEnable": 1,
        "ProxyServer": _proxy_address(host, port),
        "ProxyOverride": BYPASS,
        # A PAC script would take precedence over the manual proxy.
        "AutoConfigURL": None,
    })
    log.info("System proxy set to %s", _proxy_address(host, port))
    return prev


def restore(snapshot: dict) -> None:
    """Put back the settings captured by enable()."""
    _write({name: snapshot.get(name) for name in VALUES})
    log.info("System proxy restored")


def is_ours(host: str, port: int) -> bool:
    """True if the system proxy currently points at host:port."""
    try:
        cur = read()
    except OSError:
        return False
    return bool(cur.get("ProxyEnable")) and cur.get("ProxyServer") == _proxy_address(host, port)


# --- crash safety ------------------------------------------------------------
# The previous settings are also kept on disk while the proxy is active, so a
# crash / kill doesn't leave the machine pointing at a dead proxy.

def save_backup(path: str, host: str, port: int, snapshot: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"host": host, "port": port, "snapshot": snapshot}, f)


def clear_backup(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def recover(path: str) -> bool:
    """Restore settings left behind by a previous run that didn't exit cleanly."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    restored = False
    if supported() and is_ours(data.get("host", ""), int(data.get("port", 0))):
        restore(data.get("snapshot", {}))
        restored = True
    clear_backup(path)
    return restored
