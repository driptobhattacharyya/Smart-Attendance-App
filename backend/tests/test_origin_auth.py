import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SECRET = "s3cret-value"


@pytest.fixture
def make_client(settings, db, monkeypatch):
    def _make(secret: str | None) -> TestClient:
        if secret is None:
            monkeypatch.delenv("ORIGIN_SECRET", raising=False)
        else:
            monkeypatch.setenv("ORIGIN_SECRET", secret)
        return TestClient(create_app(settings, db=db, face_engine=None))

    return _make


def test_disabled_when_secret_unset(make_client):
    with make_client(None) as client:
        assert client.get("/api/auth/me").status_code == 401  # reaches auth, not blocked


def test_health_is_exempt(make_client):
    with make_client(SECRET) as client:
        assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-Origin-Auth": "wrong"}, {"X-Origin-Auth": ""}])
def test_rejects_missing_or_wrong_secret(make_client, headers):
    with make_client(SECRET) as client:
        assert client.get("/api/auth/me", headers=headers).status_code == 403


def test_accepts_correct_secret(make_client):
    with make_client(SECRET) as client:
        resp = client.get("/api/auth/me", headers={"X-Origin-Auth": SECRET})
        assert resp.status_code == 401  # past the origin check, now needs a JWT
