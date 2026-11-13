import base64
import json

import httpx
import pytest

from attendify_desk.api import ApiError, DeskApi, InvalidCode
from attendify_desk.config import Config, ConfigStore, FakeProtector, migrate_server_url
from attendify_desk.pairing import is_valid_code, normalize_code, pair_device

UUID = "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901"
BEACON = {"id": "b1", "name": "Desk 14", "uuid": UUID}
SECRET = bytes(range(32, 64))
SECRET_B64 = base64.b64encode(SECRET).decode()


def pair_reply(**overrides):
    body = {"device_token": "adt_secret", "beacon": BEACON, "secret": SECRET_B64,
            "employee": {"employee_id": "e1", "name": "Ananya Sen"},
            "heartbeat_interval_seconds": 45}
    body.update(overrides)
    return httpx.Response(200, json=body)


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
        return pair_reply()

    store = make_store(tmp_path)
    api = DeskApi("https://x.test/api/v1/", httpx.Client(transport=httpx.MockTransport(handler)))
    cfg = pair_device(store, api, "abcd2345")
    assert seen["url"] == "https://x.test/api/v1/desk/pair"
    assert seen["body"]["code"] == "ABCD-2345"
    raw = (tmp_path / "config.json").read_text()
    assert "adt_secret" not in raw and SECRET_B64 not in raw
    assert "secret_enc" in json.loads(raw)
    loaded = store.load()
    assert loaded.device_token == "adt_secret"
    assert loaded.secret == SECRET and loaded.beacon_uuid == UUID
    assert loaded.employee_name == "Ananya Sen" and loaded.heartbeat_interval == 45
    assert cfg.beacon_name == "Desk 14" and cfg.secret == SECRET


def test_pair_rejects_bad_secret(tmp_path):
    for bad in ({"secret": "not base64!"}, {"secret": base64.b64encode(b"short").decode()},
                {"secret": None}):
        api = DeskApi("https://x.test/api/v1", httpx.Client(transport=httpx.MockTransport(
            lambda r, bad=bad: pair_reply(**bad))))
        with pytest.raises(ApiError):
            pair_device(make_store(tmp_path), api, "ABCD-2345")
    assert not make_store(tmp_path).load().paired


def test_server_url_migrates_from_api_to_api_v1():
    assert migrate_server_url("https://x.test/api") == "https://x.test/api/v1"
    assert migrate_server_url("https://x.test/api/") == "https://x.test/api/v1"
    assert migrate_server_url("https://x.test/api/v1") == "https://x.test/api/v1"
    assert migrate_server_url("https://x.test/other") == "https://x.test/other"


def test_stored_config_is_migrated_on_load(tmp_path):
    store = make_store(tmp_path)
    store.save(Config(server_url="https://x.test/api/", device_token="adt_t",
                      secret=SECRET, beacon_uuid=UUID))
    assert store.load().server_url == "https://x.test/api/v1"
    assert store.load().paired


def test_pre_rotation_config_needs_repairing(tmp_path):
    # Config written before rotating identities: a fixed beacon and no secret.
    store = make_store(tmp_path)
    store.save(Config(device_token="adt_old", secret=SECRET, beacon_uuid=UUID))
    raw = json.loads((tmp_path / "config.json").read_text())
    del raw["secret_enc"]
    raw["beacon"] = {"uuid": UUID, "major": 7, "minor": 14}
    (tmp_path / "config.json").write_text(json.dumps(raw))
    assert not store.load().paired


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
    api = DeskApi("https://custom.test/api/v1", httpx.Client(transport=httpx.MockTransport(
        lambda r: pair_reply(device_token="adt_x"))))
    pair_device(store, api, "ABCD-2345")
    cfg = store.clear_token()
    assert not cfg.paired and cfg.server_url == "https://custom.test/api/v1"
    saved = json.loads((tmp_path / "config.json").read_text())
    assert "token_enc" not in saved and "secret_enc" not in saved
