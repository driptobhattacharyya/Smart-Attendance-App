import base64
import datetime as dt
import json
import logging

import httpx
import pytest

from attendify_desk.activity import FakeActivity
from attendify_desk.advertiser import AdvStatus, BluetoothState, FakeAdvertiser
from attendify_desk.agent import BT_RETRY_SECONDS, HEARTBEAT_BACKOFF_CAP, Agent
from attendify_desk.api import DeskApi
from attendify_desk.beacon import BeaconIdentity, rotating_ids, window_for
from attendify_desk.config import Config, ConfigStore, FakeProtector
from attendify_desk.pairing import pair_device
from attendify_desk.status import describe

UUID = "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901"
SECRET = bytes(range(32, 64))
SECRET_B64 = base64.b64encode(SECRET).decode()
T0 = 1_800_000_000  # exactly on a window boundary (window 6_000_000)


def ident(unix: float) -> BeaconIdentity:
    major, minor = rotating_ids(SECRET, window_for(unix))
    return BeaconIdentity(UUID, major, minor)


class Clock:
    """Fake unix clock shared by the agent and the fake server."""

    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Server:
    def __init__(self, clock: Clock):
        self.clock = clock
        self.mode = "ok"
        self.offset = 0  # server clock minus true clock, seconds
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request):
        self.requests.append(req)
        if self.mode == "network":
            raise httpx.ConnectError("down")
        if self.mode == "401":
            return httpx.Response(401, json={"detail": "no"})
        server_time = dt.datetime.fromtimestamp(self.clock() + self.offset, dt.UTC)
        return httpx.Response(200, json={
            "heartbeat_interval_seconds": 60,
            "paired": True,
            "server_time": server_time.isoformat(timespec="seconds").replace("+00:00", "Z"),
        })


def build(tmp_path, bt=BluetoothState.ON):
    store = ConfigStore(tmp_path / "c.json", FakeProtector())
    store.save(Config(device_token="adt_tok", secret=SECRET, beacon_uuid=UUID,
                      beacon_name="Desk 14", employee_name="Ananya Sen"))
    clock = Clock(T0)
    server = Server(clock)
    adv = FakeAdvertiser(bt)
    events = {"unpaired": 0, "changes": 0}
    agent = Agent(
        store,
        lambda url: DeskApi(url, httpx.Client(transport=httpx.MockTransport(server))),
        FakeActivity(locked=False, idle_seconds=12),
        adv,
        on_change=lambda: events.__setitem__("changes", events["changes"] + 1),
        on_unpaired=lambda: events.__setitem__("unpaired", events["unpaired"] + 1),
        wall_clock=lambda: dt.datetime(2026, 1, 1, 12, 3),
        unix_clock=clock,
    )
    agent.load()
    return agent, server, adv, store, events, clock


def at(agent, clock, t):
    """Run one tick at `t` seconds after T0 (monotonic and unix time move together)."""
    clock.t = T0 + t
    agent.tick(t)


def test_success_sends_contract_body_and_advertises(tmp_path):
    agent, server, adv, _, _, clock = build(tmp_path)
    at(agent, clock, 0)
    assert adv.start_calls == [ident(T0)]
    req = server.requests[0]
    assert req.headers["Authorization"] == "Device adt_tok"
    assert req.url.path.endswith("/desk/heartbeat")
    body = json.loads(req.content)
    assert body["locked"] is False and body["idle_seconds"] == 12
    assert body["advertising"] is False and body["agent_version"]  # sent before the start
    assert "major" not in body and "minor" not in body
    s = agent.state
    assert s.connected and s.last_sync.hour == 12
    v = describe(s)
    assert v.colour == "green"
    assert "Broadcasting as Desk 14" in v.lines and "Last sync 12:03" in v.lines
    assert "Assigned to Ananya Sen" in v.lines
    # Not due again until the interval passes.
    at(agent, clock, 30)
    assert len(server.requests) == 1
    at(agent, clock, 61)
    assert len(server.requests) == 2


def test_heartbeat_without_major_minor_works(tmp_path):
    # Old servers sent a fixed "beacon" with major/minor: ignore it and use the rotating one.
    agent, _, adv, _, _, clock = build(tmp_path)
    server_reply = {"heartbeat_interval_seconds": 60, "paired": True, "beacon": {
        "uuid": UUID, "major": 1, "minor": 2}}
    agent._api = DeskApi(agent._cfg.server_url, httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=server_reply))))
    at(agent, clock, 0)
    assert adv.start_calls == [ident(T0)]
    assert adv.start_calls[0] != BeaconIdentity(UUID, 1, 2)
    assert agent.state.connected


def test_network_error_backs_off_and_keeps_advertising(tmp_path):
    agent, server, adv, _, _, clock = build(tmp_path)
    at(agent, clock, 0)
    server.mode = "network"
    t = 60.0
    delays = []
    for _ in range(8):
        at(agent, clock, t)
        delays.append(agent._next_heartbeat - t)
        t = agent._next_heartbeat
    assert delays[:3] == [10, 20, 40]
    assert max(delays) == HEARTBEAT_BACKOFF_CAP
    # The outage itself never stops the publisher. The only restarts are window rotations
    # (the loop spans ~16 minutes), each of which is a stop followed by a start.
    assert adv.status() == AdvStatus.STARTED
    assert adv.stop_calls == len(adv.start_calls) - 1
    assert adv.current == ident(clock.t)  # broadcasting the window of the last tick
    assert not agent.state.connected
    assert describe(agent.state).colour == "amber"
    # Recovery resets backoff.
    server.mode = "ok"
    at(agent, clock, t)
    assert agent.state.connected and agent._next_heartbeat - t == 60


def test_401_unpairs(tmp_path):
    agent, server, adv, store, events, clock = build(tmp_path)
    at(agent, clock, 0)
    server.mode = "401"
    at(agent, clock, 61)
    assert events["unpaired"] == 1
    assert not agent.state.paired
    assert adv.status() == AdvStatus.STOPPED
    assert not store.load().paired
    assert describe(agent.state).colour == "grey"
    assert describe(agent.state).lines[0] == "This computer was unpaired"
    n = len(server.requests)
    at(agent, clock, 500)  # no more traffic once unpaired
    assert len(server.requests) == n


def test_bluetooth_off_retries_every_30s(tmp_path):
    agent, server, adv, _, _, clock = build(tmp_path, bt=BluetoothState.OFF)
    at(agent, clock, 0)
    assert adv.start_calls == [] and not agent.state.advertising
    assert describe(agent.state).lines[0] == "Bluetooth is off"
    assert describe(agent.state).colour == "amber"
    assert json.loads(server.requests[0].content)["advertising"] is False
    adv.bt = BluetoothState.ON
    at(agent, clock, BT_RETRY_SECONDS - 1)
    assert adv.start_calls == []
    at(agent, clock, BT_RETRY_SECONDS)
    assert adv.start_calls == [ident(T0 + BT_RETRY_SECONDS)]
    assert agent.state.advertising


def test_publisher_abort_restarts_with_backoff(tmp_path):
    agent, _, adv, _, _, clock = build(tmp_path)
    at(agent, clock, 0)
    adv.abort()
    at(agent, clock, 1)
    assert len(adv.start_calls) == 1  # waiting for backoff
    at(agent, clock, 6)
    assert len(adv.start_calls) == 2 and adv.status() == AdvStatus.STARTED


def test_pause_stops_advertising_and_reports_it(tmp_path):
    agent, server, adv, _, _, clock = build(tmp_path)
    at(agent, clock, 0)
    agent.pause(10, seconds=900)
    assert adv.status() == AdvStatus.STOPPED
    at(agent, clock, 11)
    assert json.loads(server.requests[-1].content)["advertising"] is False
    assert "Paused" in describe(agent.state, 11).lines[0]
    at(agent, clock, 10 + 901)
    assert adv.status() == AdvStatus.STARTED


def test_identity_rotates_exactly_at_window_boundary(tmp_path):
    agent, _, adv, _, _, clock = build(tmp_path)
    changes = []
    last = None
    for t in range(0, 2 * 300 + 6):
        at(agent, clock, t)
        if adv.current != last:
            changes.append((t, adv.current))
            last = adv.current
    assert [t for t, _ in changes] == [0, 300, 600]
    assert [c for _, c in changes] == [ident(T0), ident(T0 + 300), ident(T0 + 600)]
    assert len(adv.start_calls) == 3  # one publisher restart per window


def test_skew_moves_the_boundary(tmp_path):
    # Server clock 30 s ahead: the window must roll over 30 s early, at local T0 + 270.
    agent, server, adv, _, _, clock = build(tmp_path)
    server.offset = 30
    changes = []
    last = None
    for t in range(0, 301):
        at(agent, clock, t)
        if adv.current != last:
            changes.append(t)
            last = adv.current
    assert changes == [0, 270]
    assert agent.clock_skew == pytest.approx(30)
    assert adv.current == BeaconIdentity(UUID, *rotating_ids(SECRET, window_for(T0 + 300)))


def test_skew_smoothing(tmp_path):
    agent, _, _, _, _, _ = build(tmp_path)
    agent._observe_skew(10)
    assert agent.clock_skew == 10  # first sample is taken as-is
    agent._observe_skew(20)
    assert agent.clock_skew == pytest.approx(12)  # 10 + 0.2 * (20 - 10)


def test_lone_skew_outlier_is_ignored(tmp_path, caplog):
    agent, _, _, _, _, _ = build(tmp_path)
    agent._observe_skew(12)
    with caplog.at_level(logging.WARNING):
        agent._observe_skew(12 + 7200)  # 2 h away from the estimate
    assert agent.clock_skew == 12
    assert any("outlier" in r.getMessage() for r in caplog.records)
    agent._observe_skew(12)  # back to normal: the outlier is forgotten
    assert agent.clock_skew == 12


def test_repeated_agreeing_outliers_reanchor_the_clock(tmp_path):
    agent, _, _, _, _, _ = build(tmp_path)
    agent._observe_skew(12)
    for sample in (7212, 7213, 7211):  # a clock that really moved by about 2 h
        agent._observe_skew(sample)
    assert agent.clock_skew == 7211


def test_secret_never_reaches_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    server_reply = {"device_token": "adt_leaky", "secret": SECRET_B64,
                    "beacon": {"uuid": UUID}, "heartbeat_interval_seconds": 60}
    store = ConfigStore(tmp_path / "c.json", FakeProtector())
    api = DeskApi("https://x.test/api/v1", httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=server_reply))))
    cfg = pair_device(store, api, "ABCD-2345")
    assert cfg.secret == SECRET

    clock = Clock(T0)
    agent = Agent(store, lambda url: api, FakeActivity(), FakeAdvertiser(),
                  unix_clock=clock)
    agent.load()
    for t in (0, 61, 122):
        at(agent, clock, t)
    logged = caplog.text
    assert SECRET_B64 not in logged
    assert "adt_leaky" not in logged
    # Tracebacks and debug reprs of the config must not print the raw secret either.
    assert repr(SECRET) not in repr(store.load())
    assert "adt_leaky" not in repr(store.load())


def test_skew_outliers_are_logged_without_secret(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    agent, server, _, _, _, clock = build(tmp_path)
    at(agent, clock, 0)
    server.offset = 7200
    at(agent, clock, 61)
    assert "outlier" in caplog.text
    assert SECRET_B64 not in caplog.text
