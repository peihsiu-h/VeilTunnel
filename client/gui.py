"""VeilTunnel client GUI (tkinter)."""

from __future__ import annotations

import logging
import os
import queue
import sys
import tkinter as tk
from tkinter import messagebox, ttk

# Ensure project root is importable when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client import sysproxy
from client.core import CONFIG_FILE, DEFAULTS, PROXY_BACKUP_FILE, ClientRunner, validate
from common.gui_common import (
    LogPane, StatusDot, detect_lang, enable_dpi_awareness, install_logging,
    pump_log_queue, set_state, set_window_icon,
)
from common.settings import data_path, load_json, save_json

log = logging.getLogger("client")

APP_NAME = "VeilTunnel Client"
LOG_FILE = data_path("logs", "client.log")

TEXT = {
    "en": {
        "settings": "Settings",
        "server": "Server URL",
        "psk": "PSK",
        "show": "Show",
        "verify": "Verify server TLS certificate (turn off for a self-signed certificate)",
        "local": "Local proxy",
        "rate": "Rate limit",
        "rate_hint": "Mbps  (0 = unlimited, upload + download combined)",
        "sysproxy": "Route all traffic: set the Windows system proxy while connected",
        "sysproxy_na": "System proxy is only available on Windows",
        "connect": "Connect",
        "disconnect": "Disconnect",
        "stopping": "Stopping…",
        "log": "Log",
        "clear": "Clear",
        "st_stopped": "Disconnected",
        "st_connecting": "Connecting…",
        "st_connected": "Connected",
        "st_reconnecting": "Reconnecting",
        "st_error": "Error",
        "proxy_hint": "Other apps can use SOCKS5 / HTTP proxy {addr}",
        "recovered": "Restored the system proxy left over from a previous run",
    },
    "zh-TW": {
        "settings": "設定",
        "server": "伺服器網址",
        "psk": "PSK 金鑰",
        "show": "顯示",
        "verify": "驗證伺服器 TLS 憑證（伺服器用自簽憑證時請取消）",
        "local": "本機 Proxy",
        "rate": "速率限制",
        "rate_hint": "Mbps（0 = 不限速，上下行合計）",
        "sysproxy": "全部流量走 VPN：連線期間自動設定 Windows 系統 Proxy",
        "sysproxy_na": "系統 Proxy 僅支援 Windows",
        "connect": "連線",
        "disconnect": "中斷連線",
        "stopping": "停止中…",
        "log": "記錄",
        "clear": "清除",
        "st_stopped": "未連線",
        "st_connecting": "連線中…",
        "st_connected": "已連線",
        "st_reconnecting": "重新連線中",
        "st_error": "錯誤",
        "proxy_hint": "其他程式可使用 SOCKS5 / HTTP Proxy {addr}",
        "recovered": "已還原上次未正常結束時留下的系統 Proxy 設定",
    },
}

STATUS_COLOR = {"stopped": "grey", "connecting": "yellow", "reconnecting": "yellow",
                "connected": "green", "error": "red"}


class ClientApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_json(CONFIG_FILE, DEFAULTS)
        self.t = TEXT[detect_lang(str(self.cfg.get("language", "auto")))]

        self.log_q: queue.Queue = queue.Queue()
        level = getattr(logging, str(self.cfg.get("log_level", "INFO")).upper(), logging.INFO)
        install_logging(self.log_q, LOG_FILE, level)

        self.status_q: queue.Queue = queue.Queue()
        self.runner = ClientRunner(lambda s, d: self.status_q.put((s, d)))
        self._active = False  # a run was started and hasn't finished yet

        self._build()
        pump_log_queue(root, self.log_q, self.log_pane)
        self._set_status("stopped", "")
        self._poll()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        if sysproxy.supported() and sysproxy.recover(PROXY_BACKUP_FILE):
            log.warning(self.t["recovered"])

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        t, root = self.t, self.root
        root.title(APP_NAME)
        root.minsize(620, 520)

        self.v_server = tk.StringVar(value=self.cfg["server"])
        self.v_psk = tk.StringVar(value=self.cfg["psk"])
        self.v_show = tk.BooleanVar(value=False)
        self.v_verify = tk.BooleanVar(value=bool(self.cfg["verify_ssl"]))
        self.v_bind = tk.StringVar(value=self.cfg["bind"])
        self.v_port = tk.StringVar(value=str(self.cfg["port"]))
        self.v_rate = tk.StringVar(value=f"{float(self.cfg['rate_limit_mbps'] or 0):g}")
        self.v_sys = tk.BooleanVar(value=bool(self.cfg["system_proxy"]) and sysproxy.supported())

        box = ttk.LabelFrame(root, text=t["settings"], padding=10)
        box.pack(fill="x", padx=10, pady=(10, 5))
        box.columnconfigure(1, weight=1)
        pad = {"padx": 4, "pady": 3}

        ttk.Label(box, text=t["server"]).grid(row=0, column=0, sticky="w", **pad)
        e_server = ttk.Entry(box, textvariable=self.v_server)
        e_server.grid(row=0, column=1, columnspan=3, sticky="ew", **pad)

        ttk.Label(box, text=t["psk"]).grid(row=1, column=0, sticky="w", **pad)
        self.e_psk = ttk.Entry(box, textvariable=self.v_psk, show="•")
        self.e_psk.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)
        c_show = ttk.Checkbutton(box, text=t["show"], variable=self.v_show,
                                 command=lambda: self.e_psk.configure(show="" if self.v_show.get() else "•"))
        c_show.grid(row=1, column=3, sticky="w", **pad)

        c_verify = ttk.Checkbutton(box, text=t["verify"], variable=self.v_verify)
        c_verify.grid(row=2, column=1, columnspan=3, sticky="w", **pad)

        ttk.Label(box, text=t["local"]).grid(row=3, column=0, sticky="w", **pad)
        local = ttk.Frame(box)
        local.grid(row=3, column=1, columnspan=3, sticky="w", **pad)
        e_bind = ttk.Entry(local, textvariable=self.v_bind, width=16)
        e_bind.pack(side="left")
        ttk.Label(local, text=" : ").pack(side="left")
        e_port = ttk.Entry(local, textvariable=self.v_port, width=7)
        e_port.pack(side="left")

        ttk.Label(box, text=t["rate"]).grid(row=4, column=0, sticky="w", **pad)
        rate = ttk.Frame(box)
        rate.grid(row=4, column=1, columnspan=3, sticky="w", **pad)
        e_rate = ttk.Entry(rate, textvariable=self.v_rate, width=7)
        e_rate.pack(side="left")
        ttk.Label(rate, text=" " + t["rate_hint"]).pack(side="left")

        c_sys = ttk.Checkbutton(box, text=t["sysproxy"] if sysproxy.supported() else t["sysproxy_na"],
                                variable=self.v_sys)
        c_sys.grid(row=5, column=1, columnspan=3, sticky="w", **pad)
        if not sysproxy.supported():
            c_sys.configure(state="disabled")

        self.inputs = [e_server, self.e_psk, c_verify, e_bind, e_port, e_rate]
        if sysproxy.supported():
            self.inputs.append(c_sys)

        bar = ttk.Frame(root, padding=(10, 2))
        bar.pack(fill="x")
        self.dot = StatusDot(bar)
        self.dot.pack(side="left", padx=(4, 6))
        self.l_status = ttk.Label(bar, text="", wraplength=430, justify="left")
        self.l_status.pack(side="left", fill="x", expand=True)
        self.btn = ttk.Button(bar, text=t["connect"], width=14, command=self._toggle)
        self.btn.pack(side="right")

        self.l_hint = ttk.Label(root, text="", foreground="#666666", padding=(14, 0))
        self.l_hint.pack(fill="x")

        logbox = ttk.LabelFrame(root, text=t["log"], padding=5)
        logbox.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.log_pane = LogPane(logbox)
        self.log_pane.pack(fill="both", expand=True)
        ttk.Button(logbox, text=t["clear"], command=self.log_pane.clear).pack(anchor="e", pady=(4, 0))

    # -- actions --------------------------------------------------------------

    def _collect(self) -> dict:
        cfg = dict(self.cfg)
        cfg.update({
            "server": self.v_server.get().strip(),
            "psk": self.v_psk.get(),
            "verify_ssl": self.v_verify.get(),
            "bind": self.v_bind.get().strip(),
            "port": self.v_port.get().strip(),
            "rate_limit_mbps": self.v_rate.get().strip() or "0",
            "system_proxy": self.v_sys.get(),
        })
        return cfg

    def _save(self) -> None:
        raw = self._collect()
        # Persist numbers as numbers when they parse; keep the text otherwise.
        for key, conv in (("port", int), ("rate_limit_mbps", float)):
            try:
                raw[key] = conv(raw[key])
            except (TypeError, ValueError):
                pass
        self.cfg = raw
        try:
            save_json(CONFIG_FILE, raw)
        except OSError as exc:
            log.warning("cannot save settings: %s", exc)

    def _toggle(self) -> None:
        if self.runner.running:
            self.btn.configure(text=self.t["stopping"], state="disabled")
            self.runner.request_stop()
            return
        try:
            cfg = validate(self._collect())
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)
            return
        self._save()
        self.runner.start(cfg)
        self._active = True
        set_state(self.inputs, False)
        self.btn.configure(text=self.t["disconnect"])
        self.l_hint.configure(text=self.t["proxy_hint"].format(addr=f"{cfg['bind']}:{cfg['port']}"))

    def _set_status(self, state: str, detail: str) -> None:
        self.dot.set(STATUS_COLOR.get(state, "grey"))
        text = self.t.get(f"st_{state}", state)
        if detail and state in ("error", "reconnecting", "connected"):
            text += f" — {detail}"
        self.l_status.configure(text=text)

    def _poll(self) -> None:
        while True:
            try:
                state, detail = self.status_q.get_nowait()
            except queue.Empty:
                break
            self._set_status(state, detail)
        if self._active and not self.runner.running:
            self._active = False
            set_state(self.inputs, True)
            self.btn.configure(text=self.t["connect"], state="normal")
            self.l_hint.configure(text="")
        self.root.after(200, self._poll)

    def _on_close(self) -> None:
        if self.runner.running:
            self.runner.stop(timeout=5)
        self._save()
        self.root.destroy()


def main() -> None:
    enable_dpi_awareness()
    root = tk.Tk()
    set_window_icon(root)
    app = ClientApp(root)
    if "--autostart" in sys.argv[1:]:  # e.g. from a Windows startup shortcut
        root.after(300, app._toggle)
    root.mainloop()


if __name__ == "__main__":
    main()
