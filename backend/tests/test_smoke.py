async def test_health(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["database"] == "up"


async def test_signup_and_me(org, client):
    r = await client.get("/api/v1/people/me", headers=org.owner_headers)
    assert r.status_code == 200, r.text
    assert r.json()["consent_required"] is True
