"""Bluetooth LE advertising: an abstraction, a fake for tests, and the WinRT implementation."""

from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum
from typing import Protocol

from .beacon import APPLE_COMPANY_ID, BeaconIdentity, build_ibeacon_payload

log = logging.getLogger(__name__)


class BluetoothState(Enum):
    ON = "on"
    OFF = "off"
    NO_ADAPTER = "no_adapter"


class AdvStatus(Enum):
    STOPPED = "stopped"
    WAITING = "waiting"  # started, but the radio is not transmitting yet
    STARTED = "started"
    ABORTED = "aborted"


class AdvertiserError(Exception):
    pass


class Advertiser(Protocol):
    last_error: str | None
    current: BeaconIdentity | None  # identity being broadcast, if any

    def bluetooth_state(self) -> BluetoothState: ...
    def start(self, identity: BeaconIdentity) -> None: ...
    def stop(self) -> None: ...
    def status(self) -> AdvStatus: ...


class FakeAdvertiser:
    """Scriptable test double."""

    def __init__(self, bt: BluetoothState = BluetoothState.ON):
        self.bt = bt
        self.current: BeaconIdentity | None = None
        self._status = AdvStatus.STOPPED
        self.start_calls: list[BeaconIdentity] = []
        self.stop_calls = 0
        self.fail_start = False
        self.last_error: str | None = None

    def bluetooth_state(self) -> BluetoothState:
        return self.bt

    def start(self, identity: BeaconIdentity) -> None:
        if self.fail_start:
            raise AdvertiserError("start failed")
        self.start_calls.append(identity)
        self.current = identity
        self._status = AdvStatus.STARTED

    def stop(self) -> None:
        self.stop_calls += 1
        self.current = None
        self._status = AdvStatus.STOPPED

    def status(self) -> AdvStatus:
        return self._status

    def abort(self) -> None:  # simulate the OS killing the publisher
        self._status = AdvStatus.ABORTED


class NullAdvertiser(FakeAdvertiser):
    """Development stand-in on non-Windows systems."""


class WinAdvertiser:
    """BluetoothLEAdvertisementPublisher wrapper.

    All calls must come from one worker thread. winrt is imported lazily, so the main
    (Tk) thread never initialises COM before it needs to.

    Mirrors Microsoft's "Bluetooth advertisement" sample: build a
    BluetoothLEManufacturerData (company id + DataWriter buffer), append it to the
    publisher's Advertisement.ManufacturerData, call Start(), then watch Status.
    """

    def __init__(self) -> None:
        self._publisher = None
        self.current: BeaconIdentity | None = None
        self.last_error: str | None = None

    # -- Bluetooth radio / adapter ------------------------------------------------
    def bluetooth_state(self) -> BluetoothState:
        try:
            return asyncio.run(self._bluetooth_state_async())
        except Exception as exc:
            log.warning("Radio query failed: %s", exc)
            self.last_error = f"Could not query Bluetooth: {exc}"
            return BluetoothState.NO_ADAPTER

    async def _bluetooth_state_async(self) -> BluetoothState:
        from winrt.windows.devices.bluetooth import BluetoothAdapter
        from winrt.windows.devices.radios import Radio, RadioAccessStatus, RadioKind, RadioState

        adapter = await BluetoothAdapter.get_default_async()
        if adapter is None:
            return BluetoothState.NO_ADAPTER
        # Informational: many adapters report False yet still broadcast fine.
        self._capable = bool(
            adapter.is_peripheral_role_supported or adapter.is_advertisement_offload_supported
        )
        access = await Radio.request_access_async()
        if access != RadioAccessStatus.ALLOWED:
            log.info("Radio access status: %s", access)
        for radio in await Radio.get_radios_async():
            if radio.kind == RadioKind.BLUETOOTH:
                return BluetoothState.ON if radio.state == RadioState.ON else BluetoothState.OFF
        return BluetoothState.NO_ADAPTER

    _capable: bool | None = None

    # -- Publisher ----------------------------------------------------------------
    def start(self, identity: BeaconIdentity) -> None:
        from winrt.windows.devices.bluetooth.advertisement import (
            BluetoothLEAdvertisementPublisher,
            BluetoothLEManufacturerData,
        )
        from winrt.windows.storage.streams import DataWriter

        self.stop()
        payload = build_ibeacon_payload(
            identity.uuid, identity.major, identity.minor, identity.measured_power
        )
        writer = DataWriter()
        writer.write_bytes(payload)
        data = BluetoothLEManufacturerData()
        data.company_id = APPLE_COMPANY_ID
        data.data = writer.detach_buffer()

        publisher = BluetoothLEAdvertisementPublisher()
        publisher.advertisement.manufacturer_data.append(data)
        try:
            publisher.start()
        except Exception as exc:
            raise AdvertiserError(f"Could not start Bluetooth advertising: {exc}") from exc
        self._publisher = publisher
        self.current = identity
        self.last_error = None
        log.info("Publisher started for major=%s minor=%s", identity.major, identity.minor)

    def stop(self) -> None:
        pub, self._publisher = self._publisher, None
        self.current = None
        if pub is not None:
            try:
                pub.stop()
            except Exception as exc:
                log.debug("publisher.stop failed: %s", exc)

    def status(self) -> AdvStatus:
        if self._publisher is None:
            return AdvStatus.STOPPED
        from winrt.windows.devices.bluetooth.advertisement import (
            BluetoothLEAdvertisementPublisherStatus as S,
        )

        st = self._publisher.status
        if st == S.STARTED:
            return AdvStatus.STARTED
        if st == S.WAITING:
            return AdvStatus.WAITING
        if st == S.ABORTED:
            err = self._publisher.error if hasattr(self._publisher, "error") else None
            if self._capable is False:
                self.last_error = (
                    "This computer's Bluetooth adapter does not appear to support "
                    "broadcasting (BLE advertising)."
                )
            else:
                self.last_error = f"Bluetooth advertising stopped unexpectedly ({err})."
            return AdvStatus.ABORTED
        return AdvStatus.STOPPED  # CREATED / STOPPING / STOPPED


def default_advertiser() -> Advertiser:
    return WinAdvertiser() if os.name == "nt" else NullAdvertiser()
