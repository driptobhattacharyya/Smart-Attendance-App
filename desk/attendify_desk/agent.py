"""The heartbeat + advertising state machine.

`Agent.tick(now)` does whatever is due and never blocks for long, so it is easy to test
with fake clocks. `run_forever` just calls it once a second from a worker thread.
`now` is a monotonic time in seconds, used for scheduling only. The rotating beacon identity
is derived from unix time (`unix_clock`) plus the clock skew learned from heartbeats.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import __version__
from .activity import ActivityProbe
from .advertiser import Advertiser, AdvertiserError, AdvStatus, BluetoothState
from .api import ApiError, DeskApi, ServerUnreachable, Unpaired
from .beacon import BeaconIdentity, rotating_ids, window_for
from .config import Config, ConfigStore

log = logging.getLogger(__name__)

HEARTBEAT_BACKOFF_BASE = 5.0
HEARTBEAT_BACKOFF_CAP = 300.0
BT_RETRY_SECONDS = 30.0
ADV_BACKOFF_BASE = 5.0
ADV_BACKOFF_CAP = 60.0
PAUSE_SECONDS = 15 * 60
SKEW_ALPHA = 0.2  # weight of each new heartbeat sample in the smoothed clock offset
SKEW_OUTLIER_SECONDS = 3600.0  # a sample this far from the estimate is ignored...
SKEW_RESYNC_AFTER = 3  # ...unless this many consecutive outliers agree (the clock really moved)
SKEW_RESYNC_AGREE_SECONDS = 60.0


@dataclass
class AgentState:
    paired: bool = False
    beacon: BeaconIdentity | None = None  # the identity being broadcast this window
    beacon_name: str | None = None
    employee_name: str | None = None
    advertising: bool = False
    bluetooth: BluetoothState = BluetoothState.ON
    adv_error: str | None = None
    connected: bool = False  # last heartbeat succeeded
    ever_synced: bool = False
    last_sync: dt.datetime | None = None
    paused_until: float | None = None
    unpaired_reason: str | None = None


class Agent:
    def __init__(
        self,
        store: ConfigStore,
        api_factory: Callable[[str], DeskApi],
        activity: ActivityProbe,
        advertiser: Advertiser,
        on_change: Callable[[], None] | None = None,
        on_unpaired: Callable[[], None] | None = None,
        wall_clock: Callable[[], dt.datetime] = dt.datetime.now,
        unix_clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.api_factory = api_factory
        self.activity = activity
        self.adv = advertiser
        self.on_change = on_change or (lambda: None)
        self.on_unpaired = on_unpaired or (lambda: None)
        self.wall_clock = wall_clock
        self.unix_clock = unix_clock
        self.state = AgentState()
        self._cfg = Config()
        self._api: DeskApi | None = None
        self._next_heartbeat = 0.0
        self._hb_failures = 0
        self._next_adv_attempt = 0.0
        self._adv_failures = 0
        self._skew: float | None = None  # smoothed server_time - local time, seconds
        self._skew_anchor: float | None = None
        self._skew_rejects = 0
        self._stop = threading.Event()
        self._lock = threading.RLock()

    # -- control ------------------------------------------------------------------
    def load(self) -> None:
        """(Re)read the config store; call after pairing/re-pairing."""
        with self._lock:
            self._cfg = self.store.load()
            self._api = self.api_factory(self._cfg.server_url) if self._cfg.paired else None
            s = self.state
            s.paired = self._cfg.paired
            s.beacon = None  # recomputed from the window on the next tick
            s.beacon_name = self._cfg.beacon_name
            s.employee_name = self._cfg.employee_name
            s.unpaired_reason = None
            self._next_heartbeat = 0.0
            self._hb_failures = 0
            self._next_adv_attempt = 0.0
            self._adv_failures = 0
            if not s.paired:
                self.adv.stop()
                s.advertising = False
        self.on_change()

    def pause(self, now: float, seconds: float = PAUSE_SECONDS) -> None:
        with self._lock:
            self.state.paused_until = now + seconds
            self.adv.stop()
            self.state.advertising = False
            self._next_heartbeat = now  # tell the server straight away
        self.on_change()

    def resume(self) -> None:
        with self._lock:
            self.state.paused_until = None
            self._next_adv_attempt = 0.0
        self.on_change()

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self.tick(time.monotonic())
                except Exception:
                    log.exception("Unexpected error in agent loop")
                self._stop.wait(1.0)
        finally:
            self.adv.stop()

    # -- identity and clock ---------------------------------------------------------
    @property
    def clock_skew(self) -> float | None:
        """Estimated server_time minus local time, in seconds (None until a heartbeat)."""
        return self._skew

    def current_window(self) -> int:
        """The 5-minute window the server will expect us to be broadcasting in."""
        return window_for(self.unix_clock() + (self._skew or 0.0))

    def _identity_now(self) -> BeaconIdentity | None:
        cfg = self._cfg
        if not (cfg.secret and cfg.beacon_uuid):
            return None
        major, minor = rotating_ids(cfg.secret, self.current_window())
        return BeaconIdentity(cfg.beacon_uuid, major, minor)

    def _observe_skew(self, sample: float) -> None:
        """Fold one heartbeat's clock offset into the estimate; reject lone wild samples."""
        if self._skew is None:
            self._skew = sample
            return
        if abs(sample - self._skew) <= SKEW_OUTLIER_SECONDS:
            self._skew_anchor = None
            self._skew_rejects = 0
            self._skew += SKEW_ALPHA * (sample - self._skew)
            return
        # An outlier. A clock that really moved gives repeated, agreeing outliers: re-anchor then.
        if self._skew_anchor is not None and (
            abs(sample - self._skew_anchor) <= SKEW_RESYNC_AGREE_SECONDS
        ):
            self._skew_rejects += 1
        else:
            self._skew_anchor = sample
            self._skew_rejects = 1
        if self._skew_rejects >= SKEW_RESYNC_AFTER:
            log.warning("Clock offset moved to %.0f s; re-anchoring", sample)
            self._skew = sample
            self._skew_anchor = None
            self._skew_rejects = 0
        else:
            log.warning(
                "Ignoring clock sample %.0f s from the current estimate (outlier)",
                sample - self._skew,
            )

    # -- the state machine -------------------------------------------------------------
    def tick(self, now: float) -> None:
        with self._lock:
            if not self.state.paired:
                return
            # Heartbeat first, so the clock skew is known before the window is chosen.
            if now >= self._next_heartbeat:
                self._heartbeat(now)
            if self.state.paired:
                self._manage_advertising(now)

    def _paused(self, now: float) -> bool:
        s = self.state
        if s.paused_until is None:
            return False
        if now >= s.paused_until:
            s.paused_until = None
            self._next_adv_attempt = 0.0
            self._next_heartbeat = now
            self.on_change()
            return False
        return True

    def _manage_advertising(self, now: float) -> None:
        s = self.state
        identity = self._identity_now()
        if identity is None:
            return
        s.beacon = identity  # changes at each window boundary
        if self._paused(now):
            return
        status = self.adv.status()
        changed = False
        if status == AdvStatus.ABORTED:
            self.adv.stop()
            s.adv_error = self.adv.last_error or "Bluetooth advertising stopped."
            self._schedule_adv_retry(now)
            log.warning("Publisher aborted: %s", s.adv_error)
            changed = True
            status = AdvStatus.STOPPED
        running = status in (AdvStatus.STARTED, AdvStatus.WAITING)
        current = self.adv.current
        # Window rolled over (or re-paired): the payload must change, so restart the publisher.
        if running and current is not None and current != identity:
            log.info("Identity window changed; restarting publisher")
            self.adv.stop()
            running = False
            self._next_adv_attempt = 0.0
        if not running and now >= self._next_adv_attempt:
            changed |= self._try_start(now)
        advertising = self.adv.status() == AdvStatus.STARTED
        if advertising != s.advertising:
            s.advertising = advertising
            changed = True
        if changed:
            self.on_change()

    def _schedule_adv_retry(self, now: float) -> None:
        delay = min(ADV_BACKOFF_CAP, ADV_BACKOFF_BASE * (2**self._adv_failures))
        self._adv_failures += 1
        self._next_adv_attempt = now + delay

    def _try_start(self, now: float) -> bool:
        s = self.state
        bt = self.adv.bluetooth_state()
        changed = bt != s.bluetooth
        s.bluetooth = bt
        if bt != BluetoothState.ON:
            s.adv_error = None
            self._next_adv_attempt = now + BT_RETRY_SECONDS
            return changed
        try:
            assert s.beacon is not None
            self.adv.start(s.beacon)
        except AdvertiserError as exc:
            s.adv_error = str(exc)
            log.warning("%s", exc)
            self._schedule_adv_retry(now)
            return True
        self._adv_failures = 0
        s.adv_error = None
        return True

    def _heartbeat(self, now: float) -> None:
        s = self.state
        assert self._api is not None and self._cfg.device_token
        sample = self.activity.sample()
        try:
            result = self._api.heartbeat(
                self._cfg.device_token,
                locked=sample.locked,
                idle_seconds=int(sample.idle_seconds),
                advertising=self.adv.status() == AdvStatus.STARTED,
                agent_version=__version__,
            )
        except Unpaired:
            log.warning("Server says this computer is unpaired")
            self.adv.stop()
            self.store.clear_token()
            s.paired = False
            s.advertising = False
            s.connected = False
            s.beacon = None
            s.employee_name = None
            s.unpaired_reason = "This computer was unpaired"
            self._cfg = self.store.load()
            self._api = None
            self.on_change()
            self.on_unpaired()
            return
        except ApiError as exc:  # network error, 5xx or unexpected reply
            self._hb_failures += 1
            delay = min(HEARTBEAT_BACKOFF_CAP, HEARTBEAT_BACKOFF_BASE * (2**self._hb_failures))
            self._next_heartbeat = now + delay
            was = s.connected
            s.connected = False
            log.warning(
                "Heartbeat failed (%s%s); retry in %ds",
                "offline: " if isinstance(exc, ServerUnreachable) else "",
                exc,
                delay,
            )
            if was or self._hb_failures == 1:
                self.on_change()
            return  # keep advertising through outages

        self._hb_failures = 0
        s.connected = True
        s.ever_synced = True
        s.last_sync = self.wall_clock()
        interval = max(10, result.heartbeat_interval)
        self._next_heartbeat = now + interval
        if result.server_unix is not None:
            self._observe_skew(result.server_unix - self.unix_clock())
        if interval != self._cfg.heartbeat_interval:
            self.store.update_interval(interval)
            self._cfg.heartbeat_interval = interval
        self.on_change()
