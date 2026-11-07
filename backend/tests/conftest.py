import hashlib

import numpy as np
import pytest
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.config import Settings
from app.face import FaceError, cosine_similarity
from app.main import create_app
from app.security import hash_password

# Office in Kolkata; a point ~50 m away and one ~5 km away.
OFFICE = {
    "name": "HQ",
    "latitude": 22.5726,
    "longitude": 88.3639,
    "radius": 150,
    "timezone": "Asia/Kolkata",
}
INSIDE = {"latitude": 22.5730, "longitude": 88.3640}
OUTSIDE = {"latitude": 22.6200, "longitude": 88.3639}


class FakeFaceEngine:
    """Images are bytes like b"face:alice"; the same name always gives the same embedding."""

    def embed(self, image_bytes: bytes) -> list[float]:
        text = image_bytes.decode(errors="ignore")
        if not text.startswith("face:"):
            raise FaceError("No face detected.")
        seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
        return np.random.default_rng(seed).normal(size=128).tolist()

    def similarity(self, a, b):
        return cosine_similarity(a, b)


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        mongodb_db="attendify_test",
        jwt_secret="test-secret-test-secret-test-secret",
    )


@pytest.fixture
def db():
    return AsyncMongoMockClient(tz_aware=True)["attendify_test"]


@pytest.fixture
def client(settings, db):
    app = create_app(settings, db=db, face_engine=FakeFaceEngine())
    with TestClient(app) as c:
        yield c


@pytest.fixture
def office_id(client, db):
    import asyncio

    result = asyncio.run(db.offices.insert_one(dict(OFFICE)))
    return str(result.inserted_id)


@pytest.fixture
def admin_token(client, db, office_id):
    import asyncio

    asyncio.run(
        db.users.insert_one(
            {
                "employee_id": "ADMIN",
                "name": "Admin",
                "email": "admin@example.com",
                "password_hash": hash_password("admin-password"),
                "role": "admin",
                "team_id": office_id,
                "office_id": office_id,
                "active": True,
            }
        )
    )
    return login(client, "ADMIN", "admin-password")


def login(client, employee_id: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"employee_id": employee_id, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def register(client, office_id: str, name: str = "Alice", email: str = "alice@example.com"):
    r = client.post(
        "/api/auth/register",
        json={
            "name": name,
            "email": email,
            "phone": "123",
            "password": "password123",
            "office_id": office_id,
            "bluetooth_device": "AA:BB",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def enroll(client, token: str, who: str = "alice"):
    r = client.post(
        "/api/face/enroll",
        headers=auth(token),
        files={"image": ("f.jpg", f"face:{who}".encode(), "image/jpeg")},
    )
    assert r.status_code == 200, r.text


def verify(client, token: str, who: str = "alice"):
    return client.post(
        "/api/face/verify",
        headers=auth(token),
        files={"image": ("f.jpg", f"face:{who}".encode(), "image/jpeg")},
    )
