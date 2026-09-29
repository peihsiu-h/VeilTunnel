"""VeilTunnel server GUI (tkinter)."""

from __future__ import annotations

import logging
import os
import queue
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Ensure project root is importable when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.gui_common import (
    LogPane, StatusDot, detect_lang, enable_dpi_awareness, install_logging,
    pump_log_queue, set_state, set_window_icon,
)
from common.settings import load_json, save_json
from server.app import WS_PATH
from server.banlist import BanList
from server.certs import fingerprint
from server.core import (
    BAN_FILE, CONFIG_FILE, DEFAULTS, LOG_FILE, ServerRunner, cert_paths, generate_psk, validate,
)

log = logging.getLogger("server")

APP_NAME = "VeilTunnel Server"

TEXT = {
    "en": {
        "tab_server": "Server",
        "tab_bans": "Ban list",
        "settings": "Settings",
        "listen": "Listen address",
        "port": "Port",
        "psk": "PSK",
        "show": "Show",
        "generate": "Generate",
        "copy": "Copy",
        "tls": "TLS (wss://) — recommended",
        "cert": "Certificate",
        "key": "Private key",
        "browse": "Browse…",
        "cert_hint": "Leave both blank to use an auto-generated self-signed certificate "
                     "(clients must then untick \"Verify server TLS certificate\").",
        "auto_ban": "Permanently ban IPs that send a wrong PSK (loopback is never banned)",
        "client_url": "Client URL",
        "fp": "Cert SHA-256",
        "start": "Start",
        "stop": "Stop",
        "stopping": "Stopping…",
        "log": "Log",
        "clear": "Clear",
        "st_stopped": "Stopped",
        "st_starting": "Starting…",
        "st_running": "Running",
        "st_error": "Error",
        "bans_hint": "IPs are banned automatically on a wrong PSK. Unbanning takes effect immediately.",
        "col_ip": "IP",
        "col_note": "Note",
        "refresh": "Refresh",
        "unban": "Unban selected",
        "open_file": "Open file",
        "no_bans": "No IPs are banned yet.",
        "regen_confirm": "Replace the current PSK? Every client must be updated with the new key.",
        "copied": "Copied to clipboard",
        "cert_files": "Certificate / key",
        "all_files": "All files",
    },
    "zh-TW": {
        "tab_server": "伺服器",
        "tab_bans": "封鎖名單",
        "settings": "設定",
        "listen": "監聽位址",
        "port": "Port",
        "psk": "PSK 金鑰",
        "show": "顯示",
        "generate": "隨機產生",
        "copy": "複製",
        "tls": "TLS（wss://）— 建議開啟",
        "cert": "憑證",
        "key": "私鑰",
        "browse": "瀏覽…",
        "cert_hint": "兩者留白 = 自動產生自簽憑證（Client 端需取消「驗證伺服器 TLS 憑證」）。",
        "auto_ban": "PSK 錯誤的 IP 自動永久封鎖（本機 loopback 不會被封鎖）",
        "client_url": "Client 連線網址",
        "fp": "憑證 SHA-256",
        "start": "啟動",
        "stop": "停止",
        "stopping": "停止中…",
        "log": "記錄",
        "clear": "清除",
        "st_stopped": "已停止",
        "st_starting": "啟動中…",
        "st_running": "執行中",
        "st_error": "錯誤",
        "bans_hint": "PSK 錯誤的 IP 會被自動封鎖；解除封鎖立即生效。",
        "col_ip": "IP",
        "col_note": "備註",
        "refresh": "重新整理",
        "unban": "解除封鎖",
        "open_file": "開啟檔案",
        "no_bans": "目前沒有被封鎖的 IP。",
        "regen_confirm": "要更換目前的 PSK 嗎？所有 Client 都必須改用新的金鑰。",
        "copied": "已複製到剪貼簿",
        "cert_files": "憑證 / 私鑰",
        "all_files": "所有檔案",
    },
}

STATUS_COLOR = {"stopped": "grey", "starting": "yellow", "running": "green", "error": "red"}


class ServerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_json(CONFIG_FILE, DEFAULTS)
        self.t = TEXT[detect_lang(str(self.cfg.get("language", "auto")))]

        self.log_q: queue.Queue = queue.Queue()
        level = getattr(logging, str(self.cfg.get("log_level", "INFO")).upper(), logging.INFO)
        install_logging(self.log_q, LOG_FILE, level)

        self.runner = ServerRunner()
        self.banlist = BanList(BAN_FILE)
        self._active = False
        self._last_state = ""

        if not self.cfg.get("psk"):
            self.cfg["psk"] = generate_psk()

        self._build()
        pump_log_queue(root, self.log_q, self.log_pane)
        self._update_url()
        self._refresh_bans()
        self._poll()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout ---------------------------------------------------------------

    def _build(self) -> None:
        t, root = self.t, self.root
        root.title(APP_NAME)
        root.minsize(660, 600)

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        tab = ttk.Frame(nb, padding=6)
        bans = ttk.Frame(nb, padding=6)
        nb.add(tab, text=t["tab_server"])
        nb.add(bans, text=t["tab_bans"])
        nb.bind("<<NotebookTabChanged>>", lambda e: self._refresh_bans())

        self.v_host = tk.StringVar(value=self.cfg["host"])
        self.v_port = tk.StringVar(value=str(self.cfg["port"]))
        self.v_psk = tk.StringVar(value=self.cfg["psk"])
        self.v_show = tk.BooleanVar(value=False)
        self.v_tls = tk.BooleanVar(value=bool(self.cfg["tls"]))
        self.v_cert = tk.StringVar(value=self.cfg["cert"])
        self.v_key = tk.StringVar(value=self.cfg["key"])
        self.v_ban = tk.BooleanVar(value=bool(self.cfg["auto_ban"]))
        self.v_url = tk.StringVar()
        self.v_fp = tk.StringVar(value="—")
        for v in (self.v_port, self.v_tls):
            v.trace_add("write", lambda *_: self._update_url())

        box = ttk.LabelFrame(tab, text=t["settings"], padding=10)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        pad = {"padx": 4, "pady": 3}

        ttk.Label(box, text=t["listen"]).grid(row=0, column=0, sticky="w", **pad)
        listen = ttk.Frame(box)
        listen.grid(row=0, column=1, columnspan=4, sticky="w", **pad)
        e_host = ttk.Entry(listen, textvariable=self.v_host, width=16)
        e_host.pack(side="left")
        ttk.Label(listen, text=f"  {t['port']} ").pack(side="left")
        e_port = ttk.Entry(listen, textvariable=self.v_port, width=7)
        e_port.pack(side="left")

        ttk.Label(box, text=t["psk"]).grid(row=1, column=0, sticky="w", **pad)
        self.e_psk = ttk.Entry(box, textvariable=self.v_psk, show="•")
        self.e_psk.grid(row=1, column=1, sticky="ew", **pad)
        c_show = ttk.Checkbutton(box, text=t["show"], variable=self.v_show,
                                 command=lambda: self.e_psk.configure(show="" if self.v_show.get() else "•"))
        c_show.grid(row=1, column=2, sticky="w", **pad)
        b_gen = ttk.Button(box, text=t["generate"], command=self._regen_psk)
        b_gen.grid(row=1, column=3, **pad)
        ttk.Button(box, text=t["copy"], command=lambda: self._copy(self.v_psk.get())).grid(row=1, column=4, **pad)

        c_tls = ttk.Checkbutton(box, text=t["tls"], variable=self.v_tls, command=self._tls_changed)
        c_tls.grid(row=2, column=1, columnspan=4, sticky="w", **pad)

        ttk.Label(box, text=t["cert"]).grid(row=3, column=0, sticky="w", **pad)
        self.e_cert = ttk.Entry(box, textvariable=self.v_cert)
        self.e_cert.grid(row=3, column=1, columnspan=3, sticky="ew", **pad)
        self.b_cert = ttk.Button(box, text=t["browse"], command=lambda: self._browse(self.v_cert))
        self.b_cert.grid(row=3, column=4, **pad)

        ttk.Label(box, text=t["key"]).grid(row=4, column=0, sticky="w", **pad)
        self.e_key = ttk.Entry(box, textvariable=self.v_key)
        self.e_key.grid(row=4, column=1, columnspan=3, sticky="ew", **pad)
        self.b_key = ttk.Button(box, text=t["browse"], command=lambda: self._browse(self.v_key))
        self.b_key.grid(row=4, column=4, **pad)

        ttk.Label(box, text=t["cert_hint"], foreground="#666666", wraplength=520,
                  justify="left").grid(row=5, column=1, columnspan=4, sticky="w", **pad)

        c_ban = ttk.Checkbutton(box, text=t["auto_ban"], variable=self.v_ban)
        c_ban.grid(row=6, column=1, columnspan=4, sticky="w", **pad)

        info = ttk.Frame(tab, padding=(4, 8, 4, 2))
        info.pack(fill="x")
        info.columnconfigure(1, weight=1)
        ttk.Label(info, text=t["client_url"]).grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(info, textvariable=self.v_url, state="readonly").grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(info, text=t["copy"], command=lambda: self._copy(self.v_url.get())).grid(row=0, column=2, padx=4)
        ttk.Label(info, text=t["fp"]).grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(info, textvariable=self.v_fp, state="readonly",
                  font=("Consolas", 8)).grid(row=1, column=1, columnspan=2, sticky="ew", padx=4)

        bar = ttk.Frame(tab, padding=(4, 6))
        bar.pack(fill="x")
        self.dot = StatusDot(bar)
        self.dot.pack(side="left", padx=(0, 6))
        self.l_status = ttk.Label(bar, text="", wraplength=460, justify="left")
        self.l_status.pack(side="left", fill="x", expand=True)
        self.btn = ttk.Button(bar, text=t["start"], width=14, command=self._toggle)
        self.btn.pack(side="right")

        logbox = ttk.LabelFrame(tab, text=t["log"], padding=5)
        logbox.pack(fill="both", expand=True)
        self.log_pane = LogPane(logbox)
        self.log_pane.pack(fill="both", expand=True)
        ttk.Button(logbox, text=t["clear"], command=self.log_pane.clear).pack(anchor="e", pady=(4, 0))

        self.inputs = [e_host, e_port, self.e_psk, b_gen, c_tls, self.e_cert, self.b_cert,
                       self.e_key, self.b_key, c_ban]
        self._tls_changed()

        # --- ban list tab ---
        ttk.Label(bans, text=t["bans_hint"], foreground="#666666").pack(anchor="w", pady=(0, 6))
        self.tree = ttk.Treeview(bans, columns=("ip", "note"), show="headings", selectmode="extended")
        self.tree.heading("ip", text=t["col_ip"])
        self.tree.heading("note", text=t["col_note"])
        self.tree.column("ip", width=200, stretch=False)
        self.tree.column("note", width=380)
        self.tree.pack(fill="both", expand=True)
        btns = ttk.Frame(bans, padding=(0, 6, 0, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text=t["refresh"], command=self._refresh_bans).pack(side="left")
        ttk.Button(btns, text=t["unban"], command=self._unban).pack(side="left", padx=6)
        ttk.Button(btns, text=t["open_file"], command=self._open_ban_file).pack(side="right")

    # -- helpers --------------------------------------------------------------

    def _copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        log.info(self.t["copied"])

    def _browse(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            filetypes=[(self.t["cert_files"], "*.pem *.crt *.cer *.key"), (self.t["all_files"], "*.*")])
        if path:
            var.set(path)

    def _regen_psk(self) -> None:
        if self.v_psk.get() and not messagebox.askyesno(APP_NAME, self.t["regen_confirm"], parent=self.root):
            return
        self.v_psk.set(generate_psk())

    def _tls_changed(self) -> None:
        if self.runner.running:
            return
        set_state([self.e_cert, self.b_cert, self.e_key, self.b_key], self.v_tls.get())

    def _update_url(self) -> None:
        scheme = "wss" if self.v_tls.get() else "ws"
        port = self.v_port.get().strip() or "?"
        self.v_url.set(f"{scheme}://<your-public-ip-or-domain>:{port}{WS_PATH}")

    def _collect(self) -> dict:
        cfg = dict(self.cfg)
        cfg.update({
            "host": self.v_host.get().strip(),
            "port": self.v_port.get().strip(),
            "psk": self.v_psk.get(),
            "tls": self.v_tls.get(),
            "cert": self.v_cert.get().strip(),
            "key": self.v_key.get().strip(),
            "auto_ban": self.v_ban.get(),
        })
        return cfg

    def _save(self) -> None:
        raw = self._collect()
        try:
            raw["port"] = int(raw["port"])
        except (TypeError, ValueError):
            pass
        self.cfg = raw
        try:
            save_json(CONFIG_FILE, raw)
        except OSError as exc:
            log.warning("cannot save settings: %s", exc)

    # -- start / stop ---------------------------------------------------------

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
        try:
            self.runner.start(cfg)
        except Exception as exc:
            log.error("%s", exc)
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)
            return
        self._active = True
        self.v_fp.set(fingerprint(cert_paths(cfg)[0]) or "—" if cfg["tls"] else "—")
        set_state(self.inputs, False)
        self.btn.configure(text=self.t["stop"])

    def _set_status(self, state: str, detail: str = "") -> None:
        key = (state, detail)
        if key == self._last_state:
            return
        self._last_state = key
        self.dot.set(STATUS_COLOR.get(state, "grey"))
        text = self.t.get(f"st_{state}", state)
        if detail:
            text += f" — {detail}"
        self.l_status.configure(text=text)

    def _poll(self) -> None:
        r = self.runner
        if r.started:
            self._set_status("running", self.v_url.get().split("://")[0] + f"://{self.cfg['host']}:{self.cfg['port']}")
        elif r.running:
            self._set_status("starting")
        elif r.error:
            self._set_status("error", r.error)
        else:
            self._set_status("stopped")

        if self._active and not r.running:
            self._active = False
            set_state(self.inputs, True)
            self._tls_changed()
            self.btn.configure(text=self.t["start"], state="normal")
        self.root.after(250, self._poll)

    # -- ban list -------------------------------------------------------------

    def _refresh_bans(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for ip, note in self.banlist.entries():
            self.tree.insert("", "end", values=(ip, note))

    def _unban(self) -> None:
        for item in self.tree.selection():
            ip = self.tree.item(item, "values")[0]
            self.banlist.unban(ip)
        self._refresh_bans()

    def _open_ban_file(self) -> None:
        if not os.path.exists(BAN_FILE):
            messagebox.showinfo(APP_NAME, self.t["no_bans"], parent=self.root)
            return
        try:
            os.startfile(BAN_FILE)  # type: ignore[attr-defined]  # Windows only
        except (AttributeError, OSError) as exc:
            messagebox.showinfo(APP_NAME, f"{BAN_FILE}\n\n{exc}", parent=self.root)

    def _on_close(self) -> None:
        if self.runner.running:
            self.runner.stop(timeout=5)
        self._save()
        self.root.destroy()


def main() -> None:
    enable_dpi_awareness()
    root = tk.Tk()
    set_window_icon(root)
    app = ServerApp(root)
    if "--autostart" in sys.argv[1:]:  # e.g. from a Windows startup shortcut
        root.after(300, app._toggle)
    root.mainloop()


if __name__ == "__main__":
    main()
