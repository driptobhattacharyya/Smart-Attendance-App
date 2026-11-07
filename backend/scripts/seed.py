"""Create demo data: one office, an admin, a manager and an employee.

    uv run python -m scripts.seed --lat 22.5726 --lon 88.3639 --tz Asia/Kolkata

Passwords are printed once. Safe to re-run: existing users are left alone.
"""

import argparse
import asyncio
import secrets

from app.config import get_settings
from app.db import create_client, ensure_indexes, next_employee_id, utcnow
from app.security import hash_password


async def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    client = create_client(settings.mongodb_uri)
    db = client[settings.mongodb_db]
    await ensure_indexes(db)

    office = await db.offices.find_one({"name": args.office})
    if not office:
        office = {
            "name": args.office,
            "latitude": args.lat,
            "longitude": args.lon,
            "radius": args.radius,
            "timezone": args.tz,
        }
        office["_id"] = (await db.offices.insert_one(office)).inserted_id
        print(f"Created office {args.office} ({args.lat}, {args.lon}, r={args.radius} m)")
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
        employee_id = await next_employee_id(db)
        await db.users.insert_one(
            {
                "employee_id": employee_id,
                "name": name,
                "email": email,
                "password_hash": hash_password(password),
                "role": role,
                "team_id": office_id,
                "office_id": office_id,
                "active": True,
                "created_at": utcnow(),
            }
        )
        print(f"{role:9} {employee_id}  {email}  password: {password}")

    await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--office", default="Head Office")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--radius", type=float, default=150)
    parser.add_argument("--tz", default="UTC")
    asyncio.run(main(parser.parse_args()))
