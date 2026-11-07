"""tkinter windows. All Tk calls happen on the main thread; other threads use `UiBus`."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from .pairing import normalize_code
from .privacy import PRIVACY_TEXT, PRIVACY_TITLE


class UiBus:
    """Thread-safe way to run a callable on the Tk main thread."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self._q: queue.Queue[Callable[[], None]] = queue.Queue()
        self._poll()

    def post(self, fn: Callable[[], None]) -> None:
        self._q.put(fn)

    def _poll(self) -> None:
        try:
            while True:
                self._q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(150, self._poll)


class PairingWindow:
    """`on_pair(server_url, code)` runs on a worker thread and returns None or raises
    an exception whose message is shown to the user."""

    def __init__(
        self,
        root: tk.Tk,
        bus: UiBus,
        default_url: str,
        on_pair: Callable[[str, str], None],
        on_done: Callable[[], None],
        message: str | None = None,
    ):
        self.bus, self.on_pair, self.on_done = bus, on_pair, on_done
        self.win = tk.Toplevel(root)
        self.win.title("Attendify Desk - pair this computer")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)
        f = ttk.Frame(self.win, padding=16)
        f.grid()
        if message:
            ttk.Label(f, text=message, foreground="#b45309").grid(sticky="w", pady=(0, 8))
        ttk.Label(f, text="Enter the pairing code your admin gave you:").grid(sticky="w")
        self.code_var = tk.StringVar()
        entry = ttk.Entry(f, textvariable=self.code_var, width=14, font=("Consolas", 16))
        entry.grid(sticky="w", pady=6)
        entry.focus_set()
        self._fixing = False
        self.code_var.trace_add("write", self._on_code)
        self.error = ttk.Label(f, text="", foreground="#b91c1c", wraplength=320)
        self.error.grid(sticky="w")
        self.url_var = tk.StringVar(value=default_url)
        self.adv_frame = ttk.Frame(f)
        ttk.Label(self.adv_frame, text="Server URL").grid(sticky="w")
        ttk.Entry(self.adv_frame, textvariable=self.url_var, width=46).grid(sticky="w")
        self.adv_btn = ttk.Button(f, text="Advanced", command=self._toggle_adv)
        self.adv_btn.grid(sticky="w", pady=(6, 0))
        self._adv_row = f.grid_size()[1]
        self.pair_btn = ttk.Button(f, text="Pair", command=self._submit)
        self.pair_btn.grid(sticky="e", pady=(10, 0))
        self.win.bind("<Return>", lambda _e: self._submit())
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.after(500, lambda: self.win.attributes("-topmost", False))

    def _on_code(self, *_a) -> None:
        if self._fixing:
            return
        self._fixing = True
        self.code_var.set(normalize_code(self.code_var.get()))
        self._fixing = False

    def _toggle_adv(self) -> None:
        if self.adv_frame.winfo_ismapped():
            self.adv_frame.grid_remove()
        else:
            self.adv_frame.grid(sticky="w", pady=4, row=self._adv_row - 1)

    def _submit(self) -> None:
        self.error.config(text="")
        self.pair_btn.state(["disabled"])
        url, code = self.url_var.get().strip(), self.code_var.get()

        def work() -> None:
            try:
                self.on_pair(url, code)
            except Exception as exc:  # shown to the user, not logged with secrets
                msg = str(exc) or "Could not pair."
                self.bus.post(lambda: self._failed(msg))
            else:
                self.bus.post(self._succeeded)

        threading.Thread(target=work, daemon=True).start()

    def _failed(self, msg: str) -> None:
        if self.win.winfo_exists():
            self.error.config(text=msg)
            self.pair_btn.state(["!disabled"])

    def _succeeded(self) -> None:
        if self.win.winfo_exists():
            self.win.destroy()
        self.on_done()

    def focus(self) -> None:
        if self.win.winfo_exists():
            self.win.lift()
            self.win.focus_force()

    def exists(self) -> bool:
        return bool(self.win.winfo_exists())


def show_privacy(root: tk.Tk) -> None:
    messagebox.showinfo(PRIVACY_TITLE, PRIVACY_TEXT, parent=root)
