import pytest
from tests.face.fakes import frame

from .helpers import API, check_in, enroll, files, ready, verify_face

pytestmark = pytest.mark.asyncio


def err(r):
    return r.json()["error"]["code"]


async def test_enroll_once_and_encrypted_at_rest(team_org, client, owner_conn):
    await ready(client, team_org, "emp1")  # enrols
    h = team_org.headers["emp1"]
    st = (await client.get(f"{API}/face/status", headers=h)).json()
    assert st["enrolled"] and st["provider"] == "opencv"
    r = await client.post(f"{API}/face/enroll", files=files(frame("emp1", seed=1)), headers=h)
    assert r.status_code == 409 and err(r) == "face_already_enrolled"
    blob = await owner_conn.fetchval("SELECT data_enc FROM face_enrollments")
    assert b"embedding" not in blob and b"opencv" not in blob and len(blob) > 100
    me = (await client.get(f"{API}/people/me", headers=h)).json()
    assert me["face_enrolled"] is True


async def test_enroll_validation(team_org, client):
    from .helpers import consent

    await consent(client, team_org, "emp1")
    h = team_org.headers["emp1"]
    r = await client.post(
        f"{API}/face/enroll", files=files(*[frame("emp1", seed=i) for i in range(4)]), headers=h
    )
    assert r.status_code == 422 and err(r) == "bad_frame_count"
    r = await client.post(f"{API}/face/enroll", files=files(frame("emp1", faces=2)), headers=h)
    assert r.status_code == 422 and err(r) == "face_rejected"
    r = await client.post(f"{API}/face/enroll", files=files(frame("emp1", spoof=0.1)), headers=h)
    assert err(r) == "face_rejected"
    assert (await client.get(f"{API}/face/status", headers=h)).json()["enrolled"] is False


async def test_face_needs_consent_and_module(team_org, client):
    h = team_org.headers["emp1"]
    r = await client.post(f"{API}/face/enroll", files=files(frame("emp1")), headers=h)
    assert err(r) == "consent_required"
    from .helpers import consent

    await consent(client, team_org, "emp1")
    await client.patch(
        f"{API}/org", json={"modules": {"face": False}}, headers=team_org.owner_headers
    )
    r = await client.get(f"{API}/face/challenge", headers=h)
    assert r.status_code == 403 and err(r) == "module_disabled"


async def test_verify_success_creates_event_and_is_consumed_once(world, client, owner_conn):
    r = await verify_face(client, world, "emp1")
    assert r.status_code == 200 and r.json()["verified"] is True
    assert r.json()["liveness_score"] > 0.5
    st = (await client.get(f"{API}/attendance/status", headers=world.headers["emp1"])).json()
    assert st["face_verified_until"] is not None
    n = await owner_conn.fetchval(
        "SELECT count(*) FROM attendance_events WHERE type='face_verified'"
    )
    assert n == 1
    assert await owner_conn.fetchval("SELECT count(*) FROM face_challenges_used") == 1


async def test_challenge_is_single_use_even_after_failure(world, client):
    h = world.headers["emp1"]
    ch = (await client.get(f"{API}/face/challenge", headers=h)).json()
    assert set(ch) >= {"challenge_id", "action", "expires_in", "token"} and ch["expires_in"] == 60
    d = 1 if ch["action"] == "turn_left" else -1
    bad = files(
        frame("mallory", 0, seed=1),
        frame("mallory", 8 * d, seed=2),
        frame("mallory", 28 * d, seed=3),
    )
    r = await client.post(
        f"{API}/face/verify", data={"challenge_token": ch["token"]}, files=bad, headers=h
    )
    assert r.status_code == 403 and err(r) == "face_verification_failed"
    assert r.json()["error"]["details"]["reason"] == "face_mismatch"
    good = files(
        frame("emp1", 0, seed=1), frame("emp1", 8 * d, seed=2), frame("emp1", 28 * d, seed=3)
    )
    r = await client.post(
        f"{API}/face/verify", data={"challenge_token": ch["token"]}, files=good, headers=h
    )
    assert r.status_code == 409 and err(r) == "challenge_used"
    audit = (
        await client.get(f"{API}/audit?action=face.verify_failed", headers=world.owner_headers)
    ).json()["items"]
    assert audit and audit[0]["after"]["reason"] == "face_mismatch"


async def test_spoof_and_challenge_failures(world, client):
    r = await verify_face(client, world, "emp1", spoof=0.1)
    assert r.status_code == 403 and r.json()["error"]["details"]["reason"] == "spoof_detected"
    h = world.headers["emp1"]
    ch = (await client.get(f"{API}/face/challenge", headers=h)).json()
    wrong_way = -1 if ch["action"] == "turn_left" else 1
    fr = files(
        frame("emp1", 0, seed=1),
        frame("emp1", 8 * wrong_way, seed=2),
        frame("emp1", 28 * wrong_way, seed=3),
    )
    r = await client.post(
        f"{API}/face/verify", data={"challenge_token": ch["token"]}, files=fr, headers=h
    )
    assert r.json()["error"]["details"]["reason"] == "challenge_failed"


async def test_challenge_bound_to_user_and_tamper_proof(world, client):
    ch = (await client.get(f"{API}/face/challenge", headers=world.headers["emp1"])).json()
    fr = files(frame("emp2", 0, seed=1), frame("emp2", 8, seed=2), frame("emp2", 28, seed=3))
    r = await client.post(
        f"{API}/face/verify",
        data={"challenge_token": ch["token"]},
        files=fr,
        headers=world.headers["emp2"],
    )
    assert r.status_code == 400 and err(r) == "invalid_challenge"
    r = await client.post(
        f"{API}/face/verify",
        data={"challenge_token": ch["token"][:-3] + "abc"},
        files=fr,
        headers=world.headers["emp1"],
    )
    assert err(r) == "invalid_challenge"


async def test_verify_requires_enrolment(team_org, client):
    from .helpers import consent

    await consent(client, team_org, "emp1")
    r = await verify_face(client, team_org, "emp1")
    assert r.status_code == 409 and err(r) == "face_not_enrolled"


async def test_too_many_failed_verifications_are_throttled(world, client):
    for _ in range(10):
        r = await verify_face(client, world, "emp1", spoof=0.1)
        assert r.status_code == 403
    r = await verify_face(client, world, "emp1")
    assert r.status_code == 429 and err(r) == "rate_limited"


async def test_who_may_delete_an_enrolment(world, client):
    uid = lambda k: world.users[k]["id"]  # noqa: E731
    # the person's manager (team) and people managers may; peers and other teams may not
    assert (
        await client.delete(f"{API}/people/{uid('emp1')}/face", headers=world.headers["emp2"])
    ).status_code == 404
    assert (
        await client.delete(f"{API}/people/{uid('direct')}/face", headers=world.headers["lead"])
    ).status_code == 404
    assert (
        await client.delete(f"{API}/people/{uid('emp1')}/face", headers=world.headers["lead"])
    ).status_code == 204
    st = (await client.get(f"{API}/face/status", headers=world.headers["emp1"])).json()
    assert st["enrolled"] is False
    assert (
        await client.delete(f"{API}/people/{uid('emp2')}/face", headers=world.headers["admin"])
    ).status_code == 204
    assert (
        await client.delete(f"{API}/people/{uid('direct')}/face", headers=world.headers["boss"])
    ).status_code == 204
    audit = (
        await client.get(f"{API}/audit?action=face.delete", headers=world.owner_headers)
    ).json()["items"]
    assert len(audit) == 3
    # can re-enrol after a reset
    await enroll(client, world, "emp1")
    # own data
    assert (await client.delete(f"{API}/face/me", headers=world.headers["emp1"])).status_code == 204


async def test_disabling_a_user_deletes_face_and_closes_the_session(world, client, owner_conn):
    await check_in(client, world, "emp1")
    r = await client.post(
        f"{API}/people/{world.users['emp1']['id']}/disable", headers=world.owner_headers
    )
    assert r.status_code == 200
    assert (
        await owner_conn.fetchval(
            "SELECT count(*) FROM face_enrollments WHERE user_id = $1::uuid",
            world.users["emp1"]["id"],
        )
        == 0
    )
    row = await owner_conn.fetchrow(
        "SELECT source, payload FROM attendance_events WHERE type='check_out'"
    )
    assert row["source"] == "system" and "account_disabled" in row["payload"]


async def test_me_and_export(world, client):
    h = world.headers["emp1"]
    await check_in(client, world, "emp1")
    me = (await client.get(f"{API}/people/me", headers=h)).json()
    assert me["office"]["radius_m"] == 150 and me["modules"]["face"] is True
    assert "attendance.self" in me["permissions"] and me["consent_required"] is False
    exp = (await client.get(f"{API}/people/me/export", headers=h)).json()
    assert exp["face"]["enrolled"] is True and "embedding" not in str(exp)
    assert {e["type"] for e in exp["events"]} >= {"check_in", "face_verified"}
    assert exp["days"] and exp["consents"]
