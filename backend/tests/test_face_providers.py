from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.config import Settings
from app.face import (
    AzureFaceProvider,
    FaceError,
    FaceUnavailableError,
    OpenCVFaceProvider,
)
from app.main import _build_face_provider

from .conftest import auth, enroll, register, verify


class HttpError(Exception):
    """Stands in for azure.core.exceptions.HttpResponseError (has ``status_code``)."""

    def __init__(self, status_code: int, message: str = "boom"):
        super().__init__(message)
        self.status_code = status_code


def _azure(faces=None, identical=True, confidence=0.9):
    face_client = MagicMock()
    face_client.detect.return_value = (
        faces if faces is not None else [SimpleNamespace(face_id="f1")]
    )
    face_client.verify_from_large_person_group.return_value = SimpleNamespace(
        is_identical=identical, confidence=confidence
    )
    admin = MagicMock()
    groups = admin.large_person_group
    groups.create_person.return_value = SimpleNamespace(person_id="p-123")
    provider = AzureFaceProvider(group_id="grp", face_client=face_client, admin_client=admin)
    return provider, face_client, groups


USER = {"employee_id": "EMP0001"}
ENROLLED = {"employee_id": "EMP0001", "azure_person_id": "p-123"}


def test_azure_is_enrolled():
    provider, _, _ = _azure()
    assert provider.name == "azure"
    assert provider.is_enrolled(USER) is False
    assert provider.is_enrolled(ENROLLED) is True


async def test_azure_enroll_creates_person_adds_face_and_trains():
    provider, face, groups = _azure()
    fields = await provider.enroll(USER, b"jpeg")
    assert fields == {"azure_person_id": "p-123"}
    groups.get.assert_called_once_with("grp")
    groups.create_person.assert_called_once()
    assert groups.create_person.call_args.args == ("grp",)
    assert groups.create_person.call_args.kwargs["name"] == "EMP0001"
    groups.add_face.assert_called_once()
    assert groups.add_face.call_args.args == ("grp", "p-123", b"jpeg")
    groups.begin_train.assert_called_once_with("grp")
    groups.begin_train.return_value.result.assert_called_once()
    assert face.detect.call_args.kwargs["return_face_id"] is True


async def test_azure_creates_group_when_missing_once():
    provider, _, groups = _azure()
    groups.get.side_effect = HttpError(404)
    await provider.enroll(USER, b"jpeg")
    await provider.enroll({"employee_id": "EMP0002"}, b"jpeg")
    groups.create.assert_called_once()
    assert groups.create.call_args.args == ("grp",)
    assert groups.create.call_args.kwargs["recognition_model"] == "recognition_04"
    assert groups.get.call_count == 1  # group readiness is remembered


async def test_azure_enroll_rejects_zero_or_many_faces_without_creating_a_person():
    for faces, message in (([], "No face"), ([SimpleNamespace(face_id="a")] * 2, "More than one")):
        provider, _, groups = _azure(faces=faces)
        with pytest.raises(FaceError, match=message):
            await provider.enroll(USER, b"jpeg")
        groups.create_person.assert_not_called()


async def test_azure_enroll_cleans_up_person_on_failure():
    provider, _, groups = _azure()
    groups.add_face.side_effect = HttpError(400)
    with pytest.raises(FaceError):
        await provider.enroll(USER, b"jpeg")
    groups.delete_person.assert_called_once_with("grp", "p-123")


async def test_azure_verify_uses_detect_then_verify_from_group():
    provider, face, _ = _azure(identical=True, confidence=0.87)
    assert await provider.verify(ENROLLED, b"jpeg") == (True, 0.87)
    face.verify_from_large_person_group.assert_called_once_with(
        face_id="f1", large_person_group_id="grp", person_id="p-123"
    )

    provider, _, _ = _azure(identical=False, confidence=0.2)
    assert await provider.verify(ENROLLED, b"jpeg") == (False, 0.2)


async def test_azure_verify_rejects_bad_photos():
    provider, _, _ = _azure(faces=[])
    with pytest.raises(FaceError, match="No face"):
        await provider.verify(ENROLLED, b"jpeg")


async def test_azure_errors_are_translated():
    provider, face, _ = _azure()
    face.detect.side_effect = HttpError(400)  # InvalidImage
    with pytest.raises(FaceError):
        await provider.verify(ENROLLED, b"jpeg")
    face.detect.side_effect = HttpError(403, "Limited Access approval required")
    with pytest.raises(FaceUnavailableError, match="Limited Access"):
        await provider.verify(ENROLLED, b"jpeg")
    face.detect.side_effect = ConnectionError("down")
    with pytest.raises(FaceUnavailableError):
        await provider.verify(ENROLLED, b"jpeg")


async def test_azure_reset_deletes_person_and_names_fields():
    provider, _, groups = _azure()
    assert await provider.reset(ENROLLED) == ["azure_person_id"]
    groups.delete_person.assert_called_once_with("grp", "p-123")

    groups.delete_person.reset_mock()
    assert await provider.reset(USER) == ["azure_person_id"]  # nothing enrolled: no API call
    groups.delete_person.assert_not_called()


async def test_azure_reset_tolerates_missing_person_but_not_outages():
    provider, _, groups = _azure()
    groups.delete_person.side_effect = HttpError(404)
    assert await provider.reset(ENROLLED) == ["azure_person_id"]
    groups.delete_person.side_effect = HttpError(500)
    with pytest.raises(FaceUnavailableError):
        await provider.reset(ENROLLED)


def test_azure_needs_credentials_or_sdk():
    with pytest.raises(RuntimeError):
        AzureFaceProvider("", "", "grp")


def test_provider_factory_degrades_gracefully():
    settings = Settings(_env_file=None, face_provider="azure")
    assert _build_face_provider(settings) is None  # missing endpoint/key -> disabled, no crash
    settings = Settings(_env_file=None, face_models_dir="/nonexistent")
    assert _build_face_provider(settings) is None  # missing models -> disabled


# ---------- OpenCV provider (engine is faked) ----------


class _Engine:
    def embed(self, image: bytes):
        if image == b"none":
            raise FaceError("No face detected.")
        return [1.0, 0.0] if image == b"alice" else [0.0, 1.0]

    def similarity(self, a, b):
        return float(np.dot(a, b))


async def test_opencv_provider_roundtrip():
    provider = OpenCVFaceProvider(_Engine(), threshold=0.5)
    assert provider.name == "opencv"
    user = {"employee_id": "E"}
    assert provider.is_enrolled(user) is False
    fields = await provider.enroll(user, b"alice")
    assert fields == {"face_embedding": [1.0, 0.0]}
    user.update(fields)
    assert provider.is_enrolled(user) is True
    assert await provider.verify(user, b"alice") == (True, 1.0)
    assert await provider.verify(user, b"bob") == (False, 0.0)
    with pytest.raises(FaceError):
        await provider.enroll(user, b"none")
    assert await provider.reset(user) == ["face_embedding"]


# ---------- through the API, with an Azure-style provider ----------


class _AzureLikeFake:
    name = "azure-like"

    def is_enrolled(self, user):
        return bool(user.get("azure_person_id"))

    async def enroll(self, user, image):
        return {"azure_person_id": "p-1"}

    async def verify(self, user, image):
        return image == b"face:alice", 0.9

    async def reset(self, user):
        self.reset_called_for = user["employee_id"]
        return ["azure_person_id"]


def test_api_works_with_provider_specific_fields(client, db, admin_token, office_id):
    import asyncio

    fake = _AzureLikeFake()
    client.app.state.face_provider = fake
    token = register(client, office_id)["access_token"]
    enroll(client, token)
    me = client.get("/api/auth/me", headers=auth(token)).json()
    assert me["face_enrolled"] is True
    user = asyncio.run(db.users.find_one({"employee_id": "EMP0001"}))
    assert user["azure_person_id"] == "p-1" and "face_embedding" not in user

    assert verify(client, token).json()["verified"] is True
    assert verify(client, token, "mallory").json()["verified"] is False

    r = client.delete("/api/manager/team/EMP0001/face", headers=auth(admin_token))
    assert r.status_code == 204 and fake.reset_called_for == "EMP0001"
    user = asyncio.run(db.users.find_one({"employee_id": "EMP0001"}))
    assert "azure_person_id" not in user and "face_enrolled_at" not in user
    assert client.get("/api/auth/me", headers=auth(token)).json()["face_enrolled"] is False


def test_provider_unavailable_maps_to_503(client, office_id):
    class Down(_AzureLikeFake):
        async def enroll(self, user, image):
            raise FaceUnavailableError("Face service error: down")

    client.app.state.face_provider = Down()
    token = register(client, office_id)["access_token"]
    r = client.post(
        "/api/face/enroll",
        headers=auth(token),
        files={"image": ("f.jpg", b"face:alice", "image/jpeg")},
    )
    assert r.status_code == 503


def test_no_provider_gives_503_and_health_flag(client, office_id):
    client.app.state.face_provider = None
    token = register(client, office_id)["access_token"]
    assert client.get("/api/health").json()["face_recognition"] is False
    r = client.post(
        "/api/face/enroll",
        headers=auth(token),
        files={"image": ("f.jpg", b"face:alice", "image/jpeg")},
    )
    assert r.status_code == 503
