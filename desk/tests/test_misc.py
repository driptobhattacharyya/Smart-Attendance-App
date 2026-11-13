import logging
import re
from pathlib import Path

from attendify_desk import __version__
from attendify_desk.activity import ActivitySample, FakeActivity, NullActivity
from attendify_desk.icon import COLOURS, make_icon
from attendify_desk.privacy import PRIVACY_TEXT
from attendify_desk.system import setup_logging

ROOT = Path(__file__).resolve().parents[1]


def test_fake_activity():
    a = FakeActivity()
    assert a.sample() == ActivitySample(False, 0)
    a.locked, a.idle_seconds = True, 700
    assert a.sample() == ActivitySample(True, 700)
    assert NullActivity().sample() == ActivitySample(False, 0)


def test_icons_render():
    for c in COLOURS:
        assert make_icon(c).size == (64, 64)


def test_privacy_text_matches_contract():
    contract = (ROOT.parent / "docs" / "api-contract-v4-desk-agent.md").read_text()
    flat = " ".join(contract.split())
    assert " ".join(PRIVACY_TEXT.split()).replace("The desk app", "The desk app") in flat


def test_version_info_matches():
    info = (ROOT / "version_info.txt").read_text()
    assert f"{__version__}.0".replace(".", ", ") in info


def test_token_and_secret_redacted_in_logs(tmp_path):
    setup_logging(tmp_path)
    secret = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    logging.getLogger("x").warning("token adt_AbC-123_x leaked")
    logging.getLogger("x").warning('pair reply {"secret": "%s"}', secret)
    logging.getLogger("x").warning("config secret=%s", secret)
    for h in logging.getLogger().handlers:
        h.flush()
    text = (tmp_path / "attendify-desk.log").read_text()
    assert "adt_***" in text and not re.search(r"adt_AbC", text)
    assert secret not in text and "***" in text
