"""Wires everything together: Tk on the main thread, pystray detached, agent in a thread."""

from __future__ import annotations

import logging
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

import pystray

from . import APP_NAME, __version__
from .activity import default_activity
from .advertiser import default_advertiser
from .agent import Agent
from .api import DeskApi
from .config import ConfigStore, default_config_path, default_protector
from .icon import make_icon
from .pairing import pair_device
from .status import describe
from .system import (
    acquire_single_instance,
    autostart_enabled,
    log_dir,
    set_autostart,
    setup_logging,
)
from .ui import PairingWindow, UiBus, show_privacy

log = logging.getLogger(__name__)


class DeskApp:
    def __init__(self) -> None:
        self.store = ConfigStore(default_config_path(), default_protector())
        self.root = tk.Tk()
        self.root.withdraw()
        self.bus = UiBus(self.root)
        self.agent = Agent(
            self.store,
            lambda url: DeskApi(url),
            default_activity(),
            default_advertiser(),
            on_change=lambda: self.bus.post(self.refresh_tray),
            on_unpaired=lambda: self.bus.post(
                lambda: self.show_pairing("This computer was unpaired")
            ),
        )
        self.pairing: PairingWindow | None = None
        self._last_colour = "grey"
        self.icon = pystray.Icon(
            "attendify-desk", make_icon("grey"), APP_NAME, menu=self._build_menu()
        )

    # -- tray -----------------------------------------------------------------------
    def _view(self):
        return describe(self.agent.state, time.monotonic())

    def _build_menu(self) -> pystray.Menu:
        M, I = pystray.Menu, pystray.MenuItem  # noqa: E741

        def status_items():
            return [I(line, None, enabled=False) for line in self._view().lines]

        def paused() -> bool:
            return self.agent.state.paused_until is not None

        return M(
            lambda: (
                *status_items(),
                M.SEPARATOR,
                I(
                    lambda _i: "Resume broadcasting" if paused()
                    else "Pause for 15 minutes (you will show as away)",
                    self._toggle_pause,
                    visible=lambda _i: self.agent.state.paired,
                ),
                I("Re-pair...", lambda: self.bus.post(lambda: self.show_pairing())),
                I(
                    "Start with Windows",
                    self._toggle_autostart,
                    checked=lambda _i: autostart_enabled(),
                    visible=os.name == "nt",
                ),
                I("What this app shares", lambda: self.bus.post(lambda: show_privacy(self.root))),
                I("Open logs", self._open_logs),
                M.SEPARATOR,
                I("Quit", self._quit),
            )
        )

    def refresh_tray(self) -> None:
        view = self._view()
        if view.colour != self._last_colour:
            self._last_colour = view.colour
            self.icon.icon = make_icon(view.colour)
        self.icon.title = f"{APP_NAME}: {view.lines[0]}"[:127]
        self.icon.update_menu()

    def _toggle_pause(self, *_a) -> None:
        if self.agent.state.paused_until is not None:
            self.agent.resume()
        else:
            self.agent.pause(time.monotonic())

    def _toggle_autostart(self, *_a) -> None:
        try:
            set_autostart(not autostart_enabled())
        except OSError:
            log.exception("Could not change startup setting")
        self.icon.update_menu()

    def _open_logs(self, *_a) -> None:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(d)  # type: ignore[attr-defined]

    def _quit(self, *_a) -> None:
        self.agent.stop()
        self.icon.stop()
        self.bus.post(self.root.quit)

    # -- pairing ----------------------------------------------------------------------
    def show_pairing(self, message: str | None = None) -> None:
        if self.pairing and self.pairing.exists():
            self.pairing.focus()
            return
        url = self.store.load().server_url

        def on_pair(server_url: str, code: str) -> None:
            pair_device(self.store, DeskApi(server_url), code)

        self.pairing = PairingWindow(
            self.root, self.bus, url, on_pair, self.agent.load, message
        )

    # -- run ----------------------------------------------------------------------------
    def run(self) -> None:
        self.agent.load()
        self.icon.run_detached()
        threading.Thread(target=self.agent.run_forever, name="agent", daemon=True).start()
        if not self.agent.state.paired:
            self.root.after(300, self.show_pairing)
        self.refresh_tray()
        self.root.mainloop()


def main() -> int:
    setup_logging()
    log.info("%s %s starting", APP_NAME, __version__)
    if not acquire_single_instance():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_NAME, "Attendify Desk is already running (see the tray icon).")
        return 0
    try:
        DeskApp().run()
    except Exception:
        log.exception("Fatal error")
        return 1
    return 0
