import asyncio
from datetime import date, timedelta

import pytest

from .helpers import API, check_in, event_types, heartbeat, loc

pytestmark = pytest.mark.asyncio
TODAY = date.today()


def err(r):
    return r.json()["error"]["code"]


def weekday(offset: int) -> date:
    d = TODAY + timedelta(days=offset)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


async def submit(client, org, key, **body):
    return await client.post(f"{API}/requests", json=body, headers=org.headers[key])


async def decide(client, org, key, rid, action="approve", comment=None):
    return await client.post(
        f"{API}/requests/{rid}/decision",
        json={"action": action, "comment": comment},
        headers=org.headers[key],
    )


CORR = {
    "kind": "correction",
    "entry_time": "09:00",
    "exit_time": "17:00",
    "reason": "forgot to check in",
}


# --------------------------------------------------------------------------- requests


async def test_correction_approval_appends_event_and_updates_the_day(world, client, owner_conn):
    day = (TODAY - timedelta(days=2)).isoformat()
    r = await submit(client, world, "emp1", start_date=day, **CORR)
    assert r.status_code == 201 and r.json()["status"] == "pending"
    rid = r.json()["id"]
    mine = (await client.get(f"{API}/requests/me", headers=world.headers["emp1"])).json()
    assert [i["id"] for i in mine["items"]] == [rid]

    d = await decide(client, world, "lead", rid, comment="ok")
    assert d.status_code == 200 and d.json()["status"] == "approved" and d.json()["comment"] == "ok"
    row = await owner_conn.fetchrow(
        "SELECT worked_minutes, status, flags FROM attendance_days WHERE day = $1",
        date.fromisoformat(day),
    )
    assert row["worked_minutes"] == 480 and row["status"] == "present"
    assert "corrected" in row["flags"]
    ev = await owner_conn.fetchrow(
        "SELECT source, actor_user_id, request_id FROM attendance_events WHERE type='interval_added'"
    )
    assert ev["source"] == "request" and str(ev["actor_user_id"]) == world.users["lead"]["id"]
    assert str(ev["request_id"]) == rid
    audit = (await client.get(f"{API}/audit?action=request.", headers=world.owner_headers)).json()[
        "items"
    ]
    assert {a["action"] for a in audit} == {"request.create", "request.approve"}


async def test_leave_approval_marks_days_on_leave(world, client, owner_conn):
    start, end = weekday(3), weekday(3) + timedelta(days=1)
    r = await submit(
        client,
        world,
        "emp1",
        kind="leave",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        reason="family event",
    )
    assert (await decide(client, world, "boss", r.json()["id"])).status_code == 200
    rows = await owner_conn.fetch("SELECT day, status FROM attendance_days ORDER BY day")
    statuses = {r["day"]: r["status"] for r in rows}
    assert statuses[start] == "on_leave"
    assert all(s in ("on_leave", "weekend") for s in statuses.values())
    # cancelling an approved future leave frees the days again
    c = await client.post(f"{API}/requests/{r.json()['id']}/cancel", headers=world.headers["emp1"])
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert start not in {
        r["day"]: 1
        for r in await owner_conn.fetch("SELECT day FROM attendance_days WHERE status='on_leave'")
    }


async def test_validation_and_module_gating(world, client):
    ok = TODAY.isoformat()
    cases = [
        (
            dict(
                kind="correction",
                start_date=(TODAY + timedelta(days=1)).isoformat(),
                **{k: v for k, v in CORR.items() if k != "kind"},
            ),
            "future_correction",
        ),
        (
            dict(
                kind="correction",
                start_date=(TODAY - timedelta(days=90)).isoformat(),
                **{k: v for k, v in CORR.items() if k != "kind"},
            ),
            "too_old",
        ),
        (
            dict(
                kind="correction",
                start_date=ok,
                entry_time="17:00",
                exit_time="09:00",
                reason="backwards time",
            ),
            "invalid_times",
        ),
        (
            dict(kind="wfh", start_date=(TODAY - timedelta(days=1)).isoformat(), reason="past wfh"),
            "past_wfh",
        ),
    ]
    for body, code in cases:
        r = await submit(client, world, "emp1", **body)
        assert err(r) == code or err(r) == "module_disabled", (body, r.text)
    r = await submit(client, world, "emp1", kind="wfh", start_date=ok, reason="plumber")
    assert err(r) == "module_disabled"  # wfh module is off by default
    r = await submit(
        client, world, "emp1", kind="correction", start_date=ok, reason="no times given"
    )
    assert r.status_code == 422
    r = await submit(
        client,
        world,
        "emp1",
        kind="leave",
        start_date=ok,
        end_date=(TODAY - timedelta(days=1)).isoformat(),
        reason="reversed",
    )
    assert r.status_code == 422
    await client.patch(
        f"{API}/org", json={"modules": {"leave": False}}, headers=world.owner_headers
    )
    r = await submit(client, world, "emp1", kind="leave", start_date=ok, reason="holiday")
    assert err(r) == "module_disabled"


async def test_overlapping_requests_conflict(world, client):
    d = weekday(5).isoformat()
    assert (
        await submit(client, world, "emp1", kind="leave", start_date=d, reason="trip one")
    ).status_code == 201
    r = await submit(client, world, "emp1", kind="leave", start_date=d, reason="trip two")
    assert r.status_code == 409 and err(r) == "request_overlap"


async def test_approval_scope_and_self_approval(world, client):
    r = await submit(
        client, world, "emp1", kind="leave", start_date=weekday(4).isoformat(), reason="appointment"
    )
    rid = r.json()["id"]
    # employees cannot decide; a manager of another branch gets 404; the person themselves 403
    assert (await decide(client, world, "emp2", rid)).status_code == 403
    other = await submit(
        client,
        world,
        "direct",
        kind="leave",
        start_date=weekday(4).isoformat(),
        reason="appointment",
    )
    assert (await decide(client, world, "lead", other.json()["id"])).status_code == 404
    assert (await decide(client, world, "lead", rid, "reject")).json()["status"] == "rejected"
    # managers cannot approve their own request; neither can an org admin
    for key in ("lead", "admin"):
        mine = await submit(
            client,
            world,
            key,
            kind="leave",
            start_date=weekday(8).isoformat(),
            reason="my own leave",
        )
        rr = await decide(client, world, key, mine.json()["id"])
        assert rr.status_code in (403, 404)
        if key == "admin":
            assert err(rr) == "cannot_approve_own"
    # an org approver may approve anyone; the owner approves the admin's request
    assert (await decide(client, world, "admin", other.json()["id"])).status_code == 200


async def test_approval_queue_is_scoped(world, client):
    a = await submit(
        client, world, "emp1", kind="leave", start_date=weekday(6).isoformat(), reason="reason a"
    )
    b = await submit(
        client, world, "direct", kind="leave", start_date=weekday(6).isoformat(), reason="reason b"
    )
    lead = (await client.get(f"{API}/requests", headers=world.headers["lead"])).json()["items"]
    assert [i["id"] for i in lead] == [a.json()["id"]] and lead[0]["user_name"] == "Emp1"
    boss = (
        await client.get(f"{API}/requests?status=pending", headers=world.headers["boss"])
    ).json()["items"]
    assert {i["id"] for i in boss} == {a.json()["id"], b.json()["id"]}
    assert (await client.get(f"{API}/requests", headers=world.headers["emp1"])).status_code == 403
    admin = (await client.get(f"{API}/requests?kind=leave", headers=world.headers["admin"])).json()[
        "items"
    ]
    assert len(admin) == 2


async def test_double_decision_and_concurrent_decisions(world, client):
    r = await submit(
        client, world, "emp1", kind="leave", start_date=weekday(7).isoformat(), reason="reason"
    )
    rid = r.json()["id"]
    results = await asyncio.gather(
        decide(client, world, "lead", rid), decide(client, world, "boss", rid, "reject")
    )
    assert sorted(x.status_code for x in results) == [200, 409]
    assert err(next(x for x in results if x.status_code == 409)) == "request_not_pending"
    assert (
        await client.post(f"{API}/requests/{rid}/cancel", headers=world.headers["emp1"])
    ).status_code in (200, 409)
    assert (
        await client.post(f"{API}/requests/{rid}/cancel", headers=world.headers["emp2"])
    ).status_code == 404


# ------------------------------------------------------------------------ corrections


async def test_admin_corrections(world, client, owner_conn):
    uid = world.users["emp2"]["id"]
    start = (TODAY - timedelta(days=1)).isoformat()
    body = {
        "user_id": uid,
        "start": f"{start}T09:00:00Z",
        "end": f"{start}T18:00:00Z",
        "kind": "work",
        "reason": "system outage",
    }
    r = await client.post(
        f"{API}/attendance/corrections/intervals", json=body, headers=world.headers["admin"]
    )
    assert r.status_code == 201 and r.json()["source"] == "admin"
    day = await owner_conn.fetchrow(
        "SELECT worked_minutes, flags FROM attendance_days WHERE user_id=$1::uuid", uid
    )
    assert day["worked_minutes"] == 540
    brk = {**body, "start": f"{start}T12:00:00Z", "end": f"{start}T13:00:00Z", "kind": "break"}
    assert (
        await client.post(
            f"{API}/attendance/corrections/intervals", json=brk, headers=world.headers["admin"]
        )
    ).status_code == 201
    assert (
        await owner_conn.fetchval(
            "SELECT worked_minutes FROM attendance_days WHERE user_id=$1::uuid", uid
        )
    ) == 480
    # void the break again
    ev = r.json()["id"]
    v = await client.post(
        f"{API}/attendance/corrections/void",
        json={"event_id": ev, "reason": "wrong day"},
        headers=world.headers["admin"],
    )
    assert v.status_code == 201 and v.json()["type"] == "event_voided"
    assert (
        await owner_conn.fetchval("SELECT status FROM attendance_days WHERE user_id=$1::uuid", uid)
    ) in ("absent", "weekend")
    again = await client.post(
        f"{API}/attendance/corrections/void",
        json={"event_id": ev, "reason": "again"},
        headers=world.headers["admin"],
    )
    assert err(again) == "already_voided"
    assert (
        err(
            await client.post(
                f"{API}/attendance/corrections/void",
                json={"event_id": v.json()["id"], "reason": "no no"},
                headers=world.headers["admin"],
            )
        )
        == "not_voidable"
    )
    actions = {
        a["action"]
        for a in (
            await client.get(f"{API}/audit?action=attendance.", headers=world.owner_headers)
        ).json()["items"]
    }
    assert {"attendance.interval_added", "attendance.event_voided"} <= actions
    # history untouched: events were only appended
    assert (await event_types(owner_conn, uid)).count("interval_added") == 2


async def test_corrections_permissions(world, client):
    uid = world.users["emp2"]["id"]
    body = {
        "user_id": uid,
        "start": "2026-01-02T09:00:00Z",
        "end": "2026-01-02T10:00:00Z",
        "reason": "reason text",
    }
    for key in ("lead", "boss", "emp1"):
        r = await client.post(
            f"{API}/attendance/corrections/intervals", json=body, headers=world.headers[key]
        )
        assert r.status_code == 403, key
    own = {**body, "user_id": world.users["admin"]["id"]}
    r = await client.post(
        f"{API}/attendance/corrections/intervals", json=own, headers=world.headers["admin"]
    )
    assert r.status_code == 403 and err(r) == "cannot_correct_own"
    assert (
        await client.post(
            f"{API}/attendance/corrections/intervals",
            json={**body, "reason": ""},
            headers=world.headers["admin"],
        )
    ).status_code == 422
    assert (
        await client.post(
            f"{API}/attendance/corrections/intervals",
            json={**body, "end": body["start"]},
            headers=world.headers["admin"],
        )
    ).status_code == 422


async def test_corrections_cannot_cross_tenants(world, make_org, client):
    other = await make_org("Other Co")
    r = await client.post(
        f"{API}/attendance/corrections/intervals",
        json={
            "user_id": world.users["emp1"]["id"],
            "start": "2026-01-02T09:00:00Z",
            "end": "2026-01-02T10:00:00Z",
            "reason": "attack",
        },
        headers=other.owner_headers,
    )
    assert r.status_code == 404
    r = await client.get(
        f"{API}/reports/people/{world.users['emp1']['id']}/days/2026-01-02",
        headers=other.owner_headers,
    )
    assert r.status_code == 404


# -------------------------------------------------------------------------------- reports


async def test_presence_board_statuses_and_scope(world, client, clock):
    await check_in(client, world, "emp1")
    far = await check_in(client, world, "emp2")
    assert far.status_code == 201
    await heartbeat(client, world, "emp2", json={"location": loc(13.5, 77.5)})  # leaves: outside
    board = (await client.get(f"{API}/reports/presence", headers=world.headers["lead"])).json()
    by = {r["name"]: r for r in board}
    assert set(by) == {"Emp1", "Emp2"}  # lead's team only
    assert (
        by["Emp1"]["status"] == "present"
        and by["Emp1"]["checked_in"]
        and by["Emp1"]["face_enrolled"]
    )
    assert by["Emp2"]["status"] == "outside" and by["Emp2"]["reason"] == "left_geofence"
    assert [r["status"] for r in board] == ["present", "outside"]  # display order
    clock.advance(minutes=30)
    board = (
        await client.get(f"{API}/reports/presence?status=stale", headers=world.headers["lead"])
    ).json()
    assert [r["name"] for r in board] == ["Emp1"]
    org = (await client.get(f"{API}/reports/presence", headers=world.headers["admin"])).json()
    assert len(org) >= 8
    assert (
        await client.get(f"{API}/reports/presence", headers=world.headers["emp1"])
    ).status_code == 403


async def test_presence_board_leave_and_unknown_filters(world, client):
    r = await submit(
        client, world, "emp1", kind="leave", start_date=TODAY.isoformat(), reason="sick today"
    )
    await decide(client, world, "lead", r.json()["id"])
    board = (
        await client.get(f"{API}/reports/presence?status=on_leave", headers=world.headers["lead"])
    ).json()
    assert [x["name"] for x in board] == ["Emp1"]
    assert (
        await client.get(f"{API}/reports/presence?status=banana", headers=world.headers["lead"])
    ).status_code == 422


async def test_dashboard(world, client):
    await check_in(client, world, "emp1")
    await submit(
        client, world, "emp2", kind="leave", start_date=weekday(9).isoformat(), reason="reason x"
    )
    d = (await client.get(f"{API}/reports/dashboard", headers=world.headers["lead"])).json()
    assert d["headcount"]["total"] == 2 and d["headcount"]["present"] == 1
    assert d["headcount"]["not_checked_in"] == 1
    assert (
        len(d["daily"]) == 14
        and d["daily"][-1]["people"] == 1
        or d["daily"][-1]["worked_minutes"] >= 0
    )
    assert d["pending_requests"] == 1
    assert d["setup"]["employees_without_face"] == 0 and d["setup"]["unpaired_desk_apps"] is None
    await world.add_user("newbie", manager="lead")
    d = (await client.get(f"{API}/reports/dashboard", headers=world.headers["lead"])).json()
    assert d["setup"]["employees_without_face"] == 1
    admin = (await client.get(f"{API}/reports/dashboard", headers=world.headers["admin"])).json()
    assert admin["headcount"]["total"] >= 8
    assert (
        await client.get(f"{API}/reports/dashboard", headers=world.headers["emp1"])
    ).status_code == 403
    past = (
        await client.get(
            f"{API}/reports/dashboard?date={(TODAY - timedelta(days=3)).isoformat()}",
            headers=world.headers["lead"],
        )
    ).json()
    assert past["headcount"]["not_checked_in"] == 3 and past["headcount"]["present"] == 0


async def test_day_and_session_detail(world, client):
    r = await check_in(client, world, "emp1")
    sid = r.json()["session"]["id"]
    await heartbeat(client, world, "emp1")
    day = TODAY.isoformat()
    d = (
        await client.get(
            f"{API}/reports/people/{world.users['emp1']['id']}/days/{day}",
            headers=world.headers["lead"],
        )
    ).json()
    assert [e["type"] for e in d["events"]] == ["face_verified", "check_in"]
    assert d["calculated"]["sessions"][0]["id"] == sid and len(d["samples"]) == 1
    assert (
        await client.get(
            f"{API}/reports/people/{world.users['emp1']['id']}/days/{day}",
            headers=world.headers["emp1"],
        )
    ).status_code == 200
    for key in ("emp2", "direct"):
        r = await client.get(
            f"{API}/reports/people/{world.users['emp1']['id']}/days/{day}",
            headers=world.headers[key],
        )
        assert r.status_code == 404
    assert (
        await client.get(
            f"{API}/reports/people/{world.users['direct']['id']}/days/{day}",
            headers=world.headers["lead"],
        )
    ).status_code == 404
    s = (await client.get(f"{API}/reports/sessions/{sid}", headers=world.headers["boss"])).json()
    assert s["session_id"] == sid and s["events"][0]["type"] == "check_in"
    assert (
        await client.get(f"{API}/reports/sessions/{sid}", headers=world.headers["direct"])
    ).status_code == 403
    assert (
        await client.get(f"{API}/reports/sessions/{sid}", headers=world.headers["lead"] | {})
    ).status_code == 200
    assert (
        await client.get(
            f"{API}/reports/sessions/00000000-0000-0000-0000-000000000000",
            headers=world.headers["lead"],
        )
    ).status_code == 404


async def test_timesheet(world, client):
    await check_in(client, world, "emp1")
    start, end = TODAY - timedelta(days=6), TODAY
    r = await client.get(
        f"{API}/reports/timesheet?start={start}&end={end}", headers=world.headers["lead"]
    )
    assert r.status_code == 200
    t = r.json()
    assert [x["name"] for x in t["rows"]] == ["Emp1", "Emp2"] and len(t["days"]) == 7
    cells = t["rows"][0]["cells"]
    assert cells[TODAY.isoformat()]["status"] == "present"
    past = [c["status"] for k, c in cells.items() if k != TODAY.isoformat()]
    assert set(past) <= {"absent", "weekend", "holiday", "on_leave"} and past
    assert t["rows"][1]["cells"][TODAY.isoformat()]["status"] is None  # today not over yet
    assert (
        await client.get(
            f"{API}/reports/timesheet?start={start}&end={start + timedelta(days=100)}",
            headers=world.headers["lead"],
        )
    ).status_code == 400
    assert (
        await client.get(
            f"{API}/reports/timesheet?start={start}&end={end}", headers=world.headers["emp1"]
        )
    ).status_code == 403
    page = (
        await client.get(
            f"{API}/reports/timesheet?start={start}&end={end}&limit=1",
            headers=world.headers["lead"],
        )
    ).json()
    assert len(page["rows"]) == 1 and page["next_cursor"]


async def test_csv_export_is_formula_safe_and_local_time(world, client, owner_conn):
    evil = '=HYPERLINK("http://evil","x")'
    await client.patch(
        f"{API}/people/{world.users['emp1']['id']}",
        json={"name": evil},
        headers=world.owner_headers,
    )
    await client.patch(
        f"{API}/places/offices/{world.office['id']}",
        json={"timezone": "Asia/Kolkata"},
        headers=world.owner_headers,
    )
    await check_in(client, world, "emp1")
    await client.post(f"{API}/attendance/check-out", json={}, headers=world.headers["emp1"])
    r = await client.get(
        f"{API}/reports/export.csv?start={TODAY}&end={TODAY}", headers=world.headers["boss"]
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("employee_code,name,office,date,status,first_in")
    assert any(l.split(",")[1].startswith("'=") or "'=HYPERLINK" in l for l in lines[1:])
    assert not any(l.startswith("=") or ",=" in l for l in lines)
    utc_hhmm = await owner_conn.fetchval(
        "SELECT to_char(first_in AT TIME ZONE 'Asia/Kolkata', 'HH24:MI') FROM attendance_days LIMIT 1"
    )
    assert utc_hhmm in r.text
    assert (
        await client.get(
            f"{API}/reports/export.csv?start={TODAY}&end={TODAY}", headers=world.headers["emp1"]
        )
    ).status_code == 403
    assert (
        await client.get(
            f"{API}/reports/export.csv?start=2026-01-01&end=2026-12-31",
            headers=world.headers["boss"],
        )
    ).status_code == 400
    actions = {
        a["action"]
        for a in (
            await client.get(f"{API}/audit?action=report.export", headers=world.owner_headers)
        ).json()["items"]
    }
    assert actions == {"report.export"}
    # team scoping: lead's export does not contain direct's rows
    lead = await client.get(
        f"{API}/reports/export.csv?start={TODAY}&end={TODAY}", headers=world.headers["lead"]
    )
    assert "Direct" not in lead.text
