import pytest
import pytest_asyncio

from .helpers import Clock, make_office, ready


@pytest.fixture
def clock(monkeypatch) -> Clock:
    return Clock(monkeypatch)


@pytest_asyncio.fixture
async def world(team_org, client):
    """team_org with an office (geofence around HQ) and everyone consented + face-enrolled."""
    office = await make_office(client, team_org)
    team_org.office = office
    for key in ("admin", "boss", "lead", "emp1", "emp2", "direct", "outsider"):
        await ready(client, team_org, key, office)
    return team_org
