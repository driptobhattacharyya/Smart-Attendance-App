"""Create demo data: one office, an admin, a manager and an employee.

    uv run python -m scripts.seed --lat 22.5726 --lon 88.3639 --tz Asia/Kolkata
    uv run python -m scripts.seed --lat 22.5726 --lon 88.3639 --tz Asia/Kolkata --demo

With ``--demo`` it also creates a second office, 12 more employees, beacons and 14 days of
realistic sessions (present/away, late arrivals, an approved leave, a pending correction) so the
admin console has something to show. The demo data is deterministic (seeded RNG).

Passwords are printed once. Safe to re-run: existing users are left alone. Seeded users have
already accepted the consent text, so the demo works without the consent screen.
"""

import argparse
import asyncio
import random
import secrets
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import create_client, ensure_indexes, next_employee_id, utcnow
from app.security import hash_password

DEMO_SEED = 20261109
DEMO_BEACON_UUID = "e2c56db5-dffb-48d2-b060-d0f5a71096e0"
DEMO_ZONE_UUID = "11111111-2222-3333-4444-555555555555"
DEMO_DAYS = 14

NAMES = [
    ("Ananya Sen", "ananya"),
    ("Rohan Gupta", "rohan"),
    ("Meera Iyer", "meera"),
    ("Imran Khan", "imran"),
    ("Sneha Das", "sneha"),
    ("Vikram Rao", "vikram"),
    ("Priya Nair", "priya"),
    ("Arjun Mehta", "arjun"),
    ("Kavya Reddy", "kavya"),
    ("Dev Malhotra", "dev"),
    ("Tara Bose", "tara"),
    ("Farhan Ali", "farhan"),
]


async def _create_user(db, name, email, role, office_id, password, *, face=False, rng=None):
    employee_id = await next_employee_id(db)
    now = utcnow()
    doc = {
        "employee_id": employee_id,
        "name": name,
        "email": email,
        "password_hash": hash_password(password),
        "role": role,
        "team_id": office_id,
        "office_id": office_id,
        "active": True,
        "created_at": now,
        "consent": {"version": get_settings().consent_version, "accepted_at": now},
    }
    if face and rng is not None:
        doc["face_embedding"] = [round(rng.gauss(0, 1), 4) for _ in range(128)]
        doc["face_enrolled_at"] = now
    await db.users.insert_one(doc)
    return employee_id


async def _ensure_office(db, name, lat, lon, radius, tz, work_start):
    office = await db.offices.find_one({"name": name})
    if not office:
        office = {
            "name": name,
            "latitude": lat,
            "longitude": lon,
            "radius": radius,
            "timezone": tz,
            "work_start": work_start,
        }
        office["_id"] = (await db.offices.insert_one(office)).inserted_id
        print(f"Created office {name} ({lat}, {lon}, r={radius} m)")
    return office


def _presence_log(rng, start, end, away_windows):
    """One entry every 5 minutes, 'away' inside the windows. Returns (log, present_s, away_s)."""
    log, present, away = [], 0, 0
    t = start
    while t + timedelta(minutes=5) <= end:
        t += timedelta(minutes=5)
        is_away = any(a <= t < b for a, b in away_windows)
        state = "away" if is_away else "present"
        log.append(
            {
                "at": t,
                "state": state,
                "distance_m": round(rng.uniform(3, 60), 1),
                "best_rssi": None if is_away else rng.randint(-78, -52),
            }
        )
        if is_away:
            away += 300
        else:
            present += 300
    return log, present, away


def _day_sessions(rng, employee_id, office, day: date, late: bool, now: datetime):
    """One to three app sessions for a working day, as documents."""
    tz = ZoneInfo(office.get("timezone", "UTC"))
    office_id = str(office["_id"])
    h, m = map(int, office.get("work_start", "09:30").split(":"))
    base = datetime.combine(day, time(h, m), tz)
    start = base + timedelta(minutes=rng.randint(25, 70) if late else rng.randint(-25, 0))
    end = start + timedelta(hours=8, minutes=rng.randint(-30, 60))
    # lunch break splits the day in two sessions most of the time
    pieces = [(start, end)]
    if rng.random() < 0.7:
        lunch = start + timedelta(hours=4, minutes=rng.randint(0, 30))
        pieces = [(start, lunch), (lunch + timedelta(minutes=rng.randint(30, 50)), end)]
    docs = []
    for s, e in pieces:
        if s.astimezone(UTC) >= now:
            continue
        e = min(e, now.astimezone(tz))
        windows = []
        for _ in range(rng.randint(0, 3)):  # away from the desk (meetings, coffee)
            a = s + timedelta(
                minutes=rng.randint(30, max(31, int((e - s).total_seconds() // 60) - 30))
            )
            windows.append((a, a + timedelta(minutes=rng.randint(10, 35))))
        log, present_s, away_s = _presence_log(rng, s, e, windows)
        docs.append(
            {
                "employee_id": employee_id,
                "team_id": office_id,
                "office_id": office_id,
                "date": day.isoformat(),
                "started_at": s.astimezone(UTC),
                "ended_at": e.astimezone(UTC),
                "source": "app",
                "last_heartbeat_at": e.astimezone(UTC),
                "last_state": "present",
                "present_seconds": present_s,
                "away_seconds": away_s,
                "presence_log": log[-500:],
                "check_in": {
                    "latitude": office["latitude"],
                    "longitude": office["longitude"],
                    "accuracy": 12.0,
                    "distance_m": round(rng.uniform(5, 80), 1),
                    "best_rssi": rng.randint(-72, -50),
                },
            }
        )
    return docs


async def _seed_demo(db, base_office, args) -> None:
    marker = f"{NAMES[0][1]}@attendify.demo"
    if await db.users.find_one({"email": marker}):
        print("Demo data already present, skipping")
        return
    rng = random.Random(DEMO_SEED)
    branch = await _ensure_office(
        db, "Branch Office", args.lat + 0.05, args.lon + 0.05, args.radius, args.tz, "10:00"
    )
    offices = [base_office, branch]
    password = secrets.token_urlsafe(9)

    people = []  # (employee_id, office)
    for i, (name, slug) in enumerate(NAMES):
        office = offices[i % 2]
        face = i != 5  # one employee has not enrolled their face yet
        eid = await _create_user(
            db, name, f"{slug}@attendify.demo", "employee", str(office["_id"]), password,
            face=face, rng=rng,
        )  # fmt: skip
        people.append((eid, office))
    print(f"demo employees ({len(people)})  password: {password}")

    # Beacons: desk beacons for the head-office people, a shared zone beacon for the branch,
    # and two spare (unassigned) ones.
    beacon_minor = 1
    for eid, office in people:
        if office is base_office and eid != people[4][0]:  # one head-office person has none
            await db.beacons.insert_one(
                {
                    "office_id": str(office["_id"]), "name": f"Desk {beacon_minor}",
                    "uuid": DEMO_BEACON_UUID, "major": 1, "minor": beacon_minor,
                    "assigned_employee_id": eid, "active": True,
                }
            )  # fmt: skip
            beacon_minor += 1
    await db.beacons.insert_one(
        {
            "office_id": str(branch["_id"]), "name": "Branch floor", "uuid": DEMO_ZONE_UUID,
            "major": 2, "minor": None, "assigned_employee_id": None, "active": True,
        }
    )  # fmt: skip
    for n in (1, 2):
        await db.beacons.insert_one(
            {
                "office_id": str(base_office["_id"]), "name": f"Spare {n}",
                "uuid": DEMO_BEACON_UUID, "major": 9, "minor": n,
                "assigned_employee_id": None, "active": True,
            }
        )  # fmt: skip

    now = utcnow()
    sessions = []
    for idx, (eid, office) in enumerate(people):
        tz = ZoneInfo(office.get("timezone", "UTC"))
        today = now.astimezone(tz).date()
        habitually_late = idx in (2, 7)
        for back in range(DEMO_DAYS - 1, -1, -1):
            day = today - timedelta(days=back)
            if day.weekday() >= 5:
                continue
            if idx == 3 and back in (6, 7, 8):  # on approved leave (see below)
                continue
            if rng.random() < 0.05:  # an occasional absence
                continue
            late = habitually_late and rng.random() < 0.6 or rng.random() < 0.08
            docs = _day_sessions(rng, eid, office, day, late, now)
            if back == 0 and docs and idx % 3 == 0:
                # still at work: leave the last session open with a fresh heartbeat
                docs[-1]["ended_at"] = None
                docs[-1]["last_heartbeat_at"] = now - timedelta(minutes=rng.randint(0, 4))
                docs[-1]["last_state"] = rng.choice(["present", "present", "away"])
            sessions.extend(docs)
    if sessions:
        await db.attendance_sessions.insert_many(sessions)

    # An approved leave (on the board it shows as "on leave" for these days), a pending correction.
    tz = ZoneInfo(people[3][1].get("timezone", "UTC"))
    today = now.astimezone(tz).date()
    leave_start = today - timedelta(days=8)
    leave_end = today - timedelta(days=6)
    req = {
        "reason": "Family wedding",
        "start_date": leave_start.isoformat(),
        "end_date": leave_end.isoformat(),
        "entry_time": None,
        "exit_time": None,
        "kind": "leave",
        "submitted_at": now - timedelta(days=12),
    }
    await db.attendance_requests.insert_one(
        {
            **req, "employee_id": people[3][0], "team_id": str(people[3][1]["_id"]),
            "status": "Approved", "decided_by": "ADMIN", "decided_at": now - timedelta(days=11),
            "comment": "Enjoy!",
        }
    )  # fmt: skip
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    await db.attendance_requests.insert_one(
        {
            "employee_id": people[1][0], "team_id": str(people[1][1]["_id"]),
            "reason": "Phone died, forgot to check in", "start_date": day.isoformat(),
            "end_date": day.isoformat(), "entry_time": "09:45", "exit_time": "18:10",
            "kind": "correction", "status": "Pending", "submitted_at": now - timedelta(hours=5),
        }
    )  # fmt: skip
    print(f"Created {len(sessions)} sessions, {await db.beacons.count_documents({})} beacons")


async def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    client = create_client(settings.mongodb_uri)
    db = client[settings.mongodb_db]
    await ensure_indexes(db)

    office = await _ensure_office(
        db, args.office, args.lat, args.lon, args.radius, args.tz, "09:30"
    )
    office_id = str(office["_id"])

    people = [
        ("admin", "Demo Admin", "admin@attendify.demo"),
        ("manager", "Demo Manager", "manager@attendify.demo"),
        ("employee", "Demo Employee", "employee@attendify.demo"),
    ]
    for role, name, email in people:
        if await db.users.find_one({"email": email}):
            print(f"{email} already exists, skipping")
            continue
        password = secrets.token_urlsafe(9)
        employee_id = await _create_user(db, name, email, role, office_id, password)
        print(f"{role:9} {employee_id}  {email}  password: {password}")

    if args.demo:
        await _seed_demo(db, office, args)

    await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--office", default="Head Office")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius", type=float, default=150)
    parser.add_argument("--tz", default="UTC")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="also create a second office, 12 employees, beacons and 14 days of sessions",
    )
    asyncio.run(main(parser.parse_args()))
