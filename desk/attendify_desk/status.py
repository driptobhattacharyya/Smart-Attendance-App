"""Turns agent state into tray colour and status lines (pure, testable)."""

from __future__ import annotations

from dataclasses import dataclass

from .advertiser import BluetoothState
from .agent import AgentState


@dataclass(frozen=True)
class StatusView:
    colour: str  # "green" | "amber" | "grey"
    lines: tuple[str, ...]


def describe(s: AgentState, now: float = 0.0) -> StatusView:
    if not s.paired:
        lines = [s.unpaired_reason or "Not paired"]
        return StatusView("grey", tuple(lines))

    lines: list[str] = []
    paused = s.paused_until is not None and now < s.paused_until
    if paused:
        mins = max(1, int((s.paused_until - now + 59) // 60))
        lines.append(f"Paused for {mins} more min (you show as away)")
    elif s.bluetooth == BluetoothState.OFF:
        lines.append("Bluetooth is off")
    elif s.bluetooth == BluetoothState.NO_ADAPTER:
        lines.append("No Bluetooth adapter found")
    elif s.adv_error:
        lines.append(s.adv_error)
    elif s.advertising:
        lines.append(f"Broadcasting as {s.beacon_name or 'this desk'}")
    else:
        lines.append("Starting Bluetooth broadcast...")

    if s.employee_name:
        lines.append(f"Assigned to {s.employee_name}")
    if s.last_sync:
        lines.append(f"Last sync {s.last_sync:%H:%M}")
    elif not s.connected:
        lines.append("Not synced yet")
    if s.ever_synced and not s.connected:
        lines.append("Offline, retrying")

    green = s.advertising and s.connected and not paused
    return StatusView("green" if green else "amber", tuple(lines))
