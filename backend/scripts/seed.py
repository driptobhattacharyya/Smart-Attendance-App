"""Demo data: one organization with an owner, an admin, two managers (a reporting tree), twelve
employees in two offices and three departments, tags and desk apps.

    uv run alembic upgrade head
    uv run python -m scripts.seed

Needs DATABASE_URL (attendify_app) and DATABASE_SYSTEM_URL (attendify_system). Safe to re-run:
does nothing if the demo organization already exists. Attendance demo data arrives with step 2.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import func, select

from app.auth.deps import Ctx, Principal
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.security import PasswordHasher
from app.people import service as people
from app.people.schemas import UserCreate
from app.places import service as places
from app.places.schemas import DeskDeviceCreate, OfficeCreate
from app.rbac.permissions import BUILTIN_ROLES
from app.tenancy.models import Organization
from app.tenancy.schemas import SignupIn
from app.tenancy.signup import signup_org

DEMO_SLUG = "demo"
DEMO_PASSWORD = "demo-password-123"
TAG_UUID = "6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901"

OFFICES = [
    OfficeCreate(
        name="Bengaluru HQ",
        latitude=12.9716,
        longitude=77.5946,
        radius_m=150,
        timezone="Asia/Kolkata",
        address="MG Road, Bengaluru",
        work_start="09:30",
        work_end="18:30",
    ),
    OfficeCreate(
        name="Mumbai Studio",
        latitude=19.0760,
        longitude=72.8777,
        radius_m=120,
        timezone="Asia/Kolkata",
        address="Bandra Kurla Complex, Mumbai",
        work_start="10:00",
        work_end="19:00",
    ),
]
DEPARTMENTS = ["Engineering", "Sales", "Operations"]
EMPLOYEES = [
    ("Asha Rao", 0, 0),
    ("Ben Thomas", 0, 0),
    ("Chitra Iyer", 0, 0),
    ("Dev Malhotra", 0, 0),
    ("Esha Nair", 0, 1),
    ("Farhan Ali", 0, 1),
    ("Gita Menon", 1, 1),
    ("Hari Prasad", 1, 1),
    ("Isha Kapoor", 1, 2),
    ("Jay Shah", 1, 2),
    ("Kavya Reddy", 1, 2),
    ("Lalit Verma", 1, 0),
]  # (name, office index, department index)


def _slug(name: str) -> str:
    return name.lower().replace(" ", ".")


async def seed(
    database: Database, settings: Settings, hasher: PasswordHasher, password: str = DEMO_PASSWORD
) -> dict | None:
    """Returns a summary, or None when the demo organization already exists."""
    async with database.system_session() as s:
        exists = (
            await s.execute(
                select(Organization.id).where(func.lower(Organization.slug) == DEMO_SLUG)
            )
        ).first()
    if exists:
        return None

    org, owner, _ = await signup_org(
        database,
        settings,
        hasher,
        SignupIn(
            org_name="Demo", name="Olivia Owner", email="owner@demo.example.com", password=password
        ),
        ip=None,
        user_agent="seed",
    )

    async with database.tenant_session(org.id) as session:
        db_org = (await session.execute(select(Organization))).scalar_one()
        role = next(r for r in BUILTIN_ROLES if r.key == "owner")
        ctx = Ctx(
            db=session,
            principal=Principal(
                user_id=owner.id,
                org_id=org.id,
                role_key="owner",
                permissions=frozenset(role.permissions),
                session_id=uuid.uuid4(),
                name=owner.name,
                email=owner.email,
            ),
            org=db_org,
            settings=settings,
            hasher=hasher,
            user_agent="seed",
        )
        db_org.modules = {**db_org.modules, "desk_app": True, "proximity": True}

        offices = [await places.create_office(ctx, o) for o in OFFICES]
        depts = [await people.create_department(ctx, n) for n in DEPARTMENTS]

        async def add(name, role_key, manager=None, office=None, dept=None):
            return await people.create_user(
                ctx,
                UserCreate(
                    name=name,
                    email=f"{_slug(name)}@demo.example.com",
                    role_key=role_key,
                    manager_id=manager.id if manager else None,
                    office_id=office.id if office else None,
                    department_id=dept.id if dept else None,
                ),
                password=password,
            )

        await add("Adam Admin", "admin", owner, offices[0], depts[2])
        mgr1 = await add("Meera Manager", "manager", owner, offices[0], depts[0])
        mgr2 = await add("Manoj Manager", "manager", mgr1, offices[1], depts[1])
        employees = []
        for i, (name, office_i, dept_i) in enumerate(EMPLOYEES):
            boss = mgr1 if i < 6 else mgr2
            employees.append(await add(name, "employee", boss, offices[office_i], depts[dept_i]))

        for i, office in enumerate(offices):
            await places.create_device(
                ctx,
                DeskDeviceCreate(
                    kind="tag",
                    name=f"{office.name} entrance tag",
                    office_id=office.id,
                    ibeacon_uuid=uuid.UUID(TAG_UUID),
                    major=100 + i,
                    minor=1,
                ),
            )
        desk_apps = []
        for employee in employees[:2]:
            desk_apps.append(
                await places.create_device(
                    ctx,
                    DeskDeviceCreate(
                        kind="desk_app",
                        name=f"{employee.name} desk",
                        office_id=employee.office_id,
                        assigned_user_id=employee.id,
                    ),
                )
            )
        code, _ = await places.create_pairing_code(ctx, desk_apps[0].id)
        return {
            "org_slug": org.slug,
            "owner_email": owner.email,
            "password": password,
            "users": 1 + 1 + 2 + len(employees),
            "offices": len(offices),
            "departments": len(depts),
            "desk_pairing_code": code,
            "desk_device_id": str(desk_apps[0].id),
        }


async def main() -> int:
    settings = get_settings()
    database = Database(settings)
    try:
        summary = await seed(database, settings, PasswordHasher(settings))
    finally:
        await database.dispose()
    if summary is None:
        print("Demo organization already exists; nothing to do.")
        return 0
    print("Seeded demo organization.")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print("Log in with the owner e-mail above, or: org_slug + employee_code E0001 / E0002 ...")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
