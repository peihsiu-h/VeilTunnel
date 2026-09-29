"""Small tkinter helpers shared by the server and client GUIs."""

from __future__ import annotations

import locale
import logging
import os
import queue
import sys
import tkinter as tk
from logging.handlers import RotatingFileHandler
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


# --- logging -----------------------------------------------------------------

class QueueLogHandler(logging.Handler):
    """Forwards log records (from any thread) to a queue drained by the GUI."""

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait((record.levelno, self.format(record)))
        except Exception:
            self.handleError(record)


def install_logging(q: queue.Queue, log_file: str | None, level: int = logging.INFO) -> None:
    """Route all logging to the GUI queue, a rotating file and (if any) stderr."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)

    qh = QueueLogHandler(q)
    qh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(qh)

    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        fh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(fh)

    # A --windowed exe has no console: sys.stderr is None there.
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(sh)


class LogPane(ttk.Frame):
    """Read-only, auto-scrolling log view with a line cap."""

    def __init__(self, master, max_lines: int = 3000, **kw):
        super().__init__(master, **kw)
        self.max_lines = max_lines
        self.text = ScrolledText(self, height=14, wrap="none", state="disabled",
                                 font=("Consolas", 9))
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("warn", foreground="#b36b00")
        self.text.tag_configure("error", foreground="#c62828")

    def append(self, levelno: int, line: str) -> None:
        tag = "error" if levelno >= logging.ERROR else "warn" if levelno >= logging.WARNING else ""
        at_bottom = self.text.yview()[1] >= 0.999
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n", tag)
        extra = int(self.text.index("end-1c").split(".")[0]) - self.max_lines
        if extra > 0:
            self.text.delete("1.0", f"{extra + 1}.0")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


def pump_log_queue(widget: tk.Misc, q: queue.Queue, pane: LogPane, interval_ms: int = 100) -> None:
    """Periodically move queued log lines into *pane* (runs on the Tk thread)."""
    def tick() -> None:
        for _ in range(500):
            try:
                levelno, line = q.get_nowait()
            except queue.Empty:
                break
            pane.append(levelno, line)
        widget.after(interval_ms, tick)
    tick()


# --- widgets -----------------------------------------------------------------

class StatusDot(tk.Canvas):
    """A small coloured circle used as a status light."""

    COLORS = {"grey": "#9e9e9e", "yellow": "#f9a825", "green": "#2e7d32", "red": "#c62828"}

    def __init__(self, master, size: int = 14, **kw):
        super().__init__(master, width=size, height=size, highlightthickness=0, **kw)
        self._dot = self.create_oval(2, 2, size - 2, size - 2, fill=self.COLORS["grey"], outline="")

    def set(self, color: str) -> None:
        self.itemconfigure(self._dot, fill=self.COLORS.get(color, color))


def set_state(widgets, enabled: bool) -> None:
    for w in widgets:
        try:
            w.configure(state="normal" if enabled else "disabled")
        except tk.TclError:
            pass


# --- platform ----------------------------------------------------------------

def resource_path(*parts: str) -> str:
    """Path to a file shipped with the app (inside the PyInstaller bundle if frozen)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


def set_window_icon(root: tk.Tk) -> None:
    try:
        root.iconbitmap(default=resource_path("assets", "veiltunnel.ico"))
    except tk.TclError:
        pass


def enable_dpi_awareness() -> None:
    """Crisp text on high-DPI Windows displays (must run before Tk())."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def detect_lang(setting: str = "auto") -> str:
    """Return "zh-TW" or "en". *setting* may force one of them."""
    if setting in ("zh-TW", "en"):
        return setting
    code = ""
    if sys.platform == "win32":
        try:
            import ctypes
            code = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
        except Exception:
            code = ""
    if not code:
        code = (locale.getlocale()[0] or "")
    return "zh-TW" if code.lower().startswith(("zh", "chinese")) else "en"
