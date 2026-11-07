import datetime as dt
import json

import httpx

from attendify_desk.activity import FakeActivity
from attendify_desk.advertiser import AdvStatus, BluetoothState, FakeAdvertiser
from attendify_desk.agent import BT_RETRY_SECONDS, HEARTBEAT_BACKOFF_CAP, Agent
from attendify_desk.api import DeskApi
from attendify_desk.beacon import BeaconIdentity
from attendify_desk.config import Config, ConfigStore, FakeProtector
from attendify_desk.status import describe

UUID = "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901"
B1 = BeaconIdentity(UUID, 7, 14)
B2 = BeaconIdentity(UUID, 7, 99)


class Server:
    def __init__(self):
        self.mode = "ok"
        self.beacon = B1
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request):
        self.requests.append(req)
        if self.mode == "network":
            raise httpx.ConnectError("down")
        if self.mode == "401":
            return httpx.Response(401, json={"detail": "no"})
        return httpx.Response(200, json={
            "beacon": self.beacon.to_json(), "heartbeat_interval_seconds": 60, "paired": True})


def build(tmp_path, bt=BluetoothState.ON):
    store = ConfigStore(tmp_path / "c.json", FakeProtector())
    store.save(Config(device_token="adt_tok", beacon=B1, beacon_name="Desk 14",
                      employee_name="Ananya Sen"))
    server = Server()
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
    )
    agent.load()
    return agent, server, adv, store, events


def test_success_sends_contract_body_and_advertises(tmp_path):
    agent, server, adv, _, _ = build(tmp_path)
    agent.tick(0)
    assert adv.start_calls == [B1]
    req = server.requests[0]
    assert req.headers["Authorization"] == "Device adt_tok"
    assert req.url.path.endswith("/desk/heartbeat")
    body = json.loads(req.content)
    assert body["locked"] is False and body["idle_seconds"] == 12
    assert body["advertising"] is True and body["agent_version"]
    s = agent.state
    assert s.connected and s.last_sync.hour == 12
    v = describe(s)
    assert v.colour == "green"
    assert "Broadcasting as Desk 14" in v.lines and "Last sync 12:03" in v.lines
    assert "Assigned to Ananya Sen" in v.lines
    # Not due again until the interval passes.
    agent.tick(30)
    assert len(server.requests) == 1
    agent.tick(61)
    assert len(server.requests) == 2


def test_network_error_backs_off_and_keeps_advertising(tmp_path):
    agent, server, adv, _, _ = build(tmp_path)
    agent.tick(0)
    server.mode = "network"
    t = 60.0
    delays = []
    for _ in range(8):
        agent.tick(t)
        delays.append(agent._next_heartbeat - t)
        t = agent._next_heartbeat
    assert delays[:3] == [10, 20, 40]
    assert max(delays) == HEARTBEAT_BACKOFF_CAP
    assert adv.status() == AdvStatus.STARTED and adv.stop_calls == 0
    assert not agent.state.connected
    assert describe(agent.state).colour == "amber"
    # Recovery resets backoff.
    server.mode = "ok"
    agent.tick(t)
    assert agent.state.connected and agent._next_heartbeat - t == 60


def test_401_unpairs(tmp_path):
    agent, server, adv, store, events = build(tmp_path)
    agent.tick(0)
    server.mode = "401"
    agent.tick(61)
    assert events["unpaired"] == 1
    assert not agent.state.paired
    assert adv.status() == AdvStatus.STOPPED
    assert not store.load().paired
    assert describe(agent.state).colour == "grey"
    assert describe(agent.state).lines[0] == "This computer was unpaired"
    n = len(server.requests)
    agent.tick(500)  # no more traffic once unpaired
    assert len(server.requests) == n


def test_identity_change_restarts_publisher(tmp_path):
    agent, server, adv, store, _ = build(tmp_path)
    agent.tick(0)
    server.beacon = B2
    agent.tick(61)  # heartbeat returns new identity
    agent.tick(62)  # advertising reconciles
    assert adv.start_calls == [B1, B2]
    assert adv.current == B2
    assert store.load().beacon == B2


def test_bluetooth_off_retries_every_30s(tmp_path):
    agent, server, adv, _, _ = build(tmp_path, bt=BluetoothState.OFF)
    agent.tick(0)
    assert adv.start_calls == [] and not agent.state.advertising
    assert describe(agent.state).lines[0] == "Bluetooth is off"
    assert describe(agent.state).colour == "amber"
    assert json.loads(server.requests[0].content)["advertising"] is False
    adv.bt = BluetoothState.ON
    agent.tick(BT_RETRY_SECONDS - 1)
    assert adv.start_calls == []
    agent.tick(BT_RETRY_SECONDS)
    assert adv.start_calls == [B1]
    assert agent.state.advertising


def test_publisher_abort_restarts_with_backoff(tmp_path):
    agent, _, adv, _, _ = build(tmp_path)
    agent.tick(0)
    adv.abort()
    agent.tick(1)
    assert len(adv.start_calls) == 1  # waiting for backoff
    agent.tick(6)
    assert len(adv.start_calls) == 2 and adv.status() == AdvStatus.STARTED


def test_pause_stops_advertising_and_reports_it(tmp_path):
    agent, server, adv, _, _ = build(tmp_path)
    agent.tick(0)
    agent.pause(10, seconds=900)
    assert adv.status() == AdvStatus.STOPPED
    agent.tick(11)
    assert json.loads(server.requests[-1].content)["advertising"] is False
    assert "Paused" in describe(agent.state, 11).lines[0]
    agent.tick(10 + 901)
    assert adv.status() == AdvStatus.STARTED
