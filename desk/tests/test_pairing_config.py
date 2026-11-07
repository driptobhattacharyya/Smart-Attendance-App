import json

import httpx
import pytest

from attendify_desk.api import DeskApi, InvalidCode
from attendify_desk.config import ConfigStore, FakeProtector
from attendify_desk.pairing import is_valid_code, normalize_code, pair_device

BEACON = {"id": "b1", "name": "Desk 14", "uuid": "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901",
          "major": 7, "minor": 14}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("abcd2345", "ABCD-2345"),
        ("abcd", "ABCD"),
        ("abcde", "ABCD-E"),
        (" ab cd-23 45 ", "ABCD-2345"),
        ("ABCD-2345-9999", "ABCD-2345"),
        ("", ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize_code(raw) == expected


def test_validity():
    assert is_valid_code("ABCD-2345")
    assert not is_valid_code("ABCD2345")
    assert not is_valid_code("ABC-2345")


def make_store(tmp_path):
    return ConfigStore(tmp_path / "config.json", FakeProtector())


def test_pair_stores_encrypted_token(tmp_path):
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "device_token": "adt_secret", "beacon": BEACON,
            "employee": {"employee_id": "e1", "name": "Ananya Sen"},
            "heartbeat_interval_seconds": 45})

    store = make_store(tmp_path)
    api = DeskApi("https://x.test/api/", httpx.Client(transport=httpx.MockTransport(handler)))
    cfg = pair_device(store, api, "abcd2345")
    assert seen["url"] == "https://x.test/api/desk/pair"
    assert seen["body"]["code"] == "ABCD-2345"
    raw = (tmp_path / "config.json").read_text()
    assert "adt_secret" not in raw
    loaded = store.load()
    assert loaded.device_token == "adt_secret"
    assert loaded.employee_name == "Ananya Sen" and loaded.heartbeat_interval == 45
    assert loaded.beacon.major == 7 and cfg.beacon_name == "Desk 14"


def test_pair_invalid_code(tmp_path):
    api = DeskApi("https://x.test", httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"detail": "x"}))))
    with pytest.raises(InvalidCode):
        pair_device(make_store(tmp_path), api, "ABCD-2345")


def test_pair_rejects_malformed_before_network(tmp_path):
    api = DeskApi("https://x.test", httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("no request expected"))))
    with pytest.raises(ValueError):
        pair_device(make_store(tmp_path), api, "AB")


def test_config_missing_and_corrupt(tmp_path):
    store = make_store(tmp_path)
    assert not store.load().paired
    (tmp_path / "config.json").write_text("{not json")
    assert not store.load().paired


def test_unreadable_token_means_unpaired(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(
        {"token_enc": "AAAA", "beacon": BEACON}))
    assert not store.load().paired


def test_clear_token_keeps_server_url(tmp_path):
    store = make_store(tmp_path)
    api = DeskApi("https://custom.test/api", httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"device_token": "adt_x", "beacon": BEACON}))))
    pair_device(store, api, "ABCD-2345")
    cfg = store.clear_token()
    assert not cfg.paired and cfg.server_url == "https://custom.test/api"
    assert "token_enc" not in json.loads((tmp_path / "config.json").read_text())
