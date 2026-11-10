"""Users, departments and reporting lines (tenant session; every mutation is audited)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from ..attendance.face_store import delete_enrollment, get_enrollment
from ..attendance.lifecycle import end_open_session
from ..auth.deps import Ctx, consent_required_for
from ..auth.emails import queue_invite_email
from ..auth.models import AuthToken
from ..auth.preauth import INVITE_PREFIX
from ..auth.sessions import revoke_user_sessions
from ..core.errors import AppError, bad_request, conflict, forbidden, not_found
from ..core.pagination import keyset_page
from ..core.security import new_token, sha256_hex, utcnow
from ..places.models import Office
from ..rbac.permissions import ORG_MANAGE, PEOPLE_MANAGE, PEOPLE_VIEW_TEAM
from ..rbac.scope import is_in_team, team_user_ids
from ..tenancy.models import Organization, Role
from ..tenancy.service import assert_seat_available
from .models import Department, User
from .schemas import MeOut, OfficeBrief, OrgBrief, UserCreate, UserUpdate


def snapshot(user: User) -> dict[str, Any]:
    return {
        "employee_code": user.employee_code,
        "email": user.email,
        "name": user.name,
        "phone": user.phone,
        "status": user.status,
        "role_key": user.role_key,
        "manager_id": user.manager_id,
        "department_id": user.department_id,
        "office_id": user.office_id,
    }


# ---------------------------------------------------------------------------------- reading


async def get_user(ctx: Ctx, user_id: uuid.UUID) -> User:
    """A user the caller may see: anyone with people.manage; else own record / reporting tree."""
    user = (await ctx.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise not_found("Person")
    if ctx.has(PEOPLE_MANAGE) or user.id == ctx.user_id:
        return user
    if ctx.has(PEOPLE_VIEW_TEAM) and await is_in_team(ctx.db, ctx.user_id, user.id):
        return user
    raise not_found("Person")  # out of scope looks exactly like missing


async def list_people(
    ctx: Ctx,
    *,
    q: str | None,
    status: str | None,
    department_id: uuid.UUID | None,
    office_id: uuid.UUID | None,
    manager_id: uuid.UUID | None,
    role_key: str | None,
    limit: int | None,
    cursor: str | None,
) -> tuple[list[User], str | None]:
    stmt = select(User)
    if not ctx.has(PEOPLE_MANAGE):
        if not ctx.has(PEOPLE_VIEW_TEAM):
            raise forbidden("You do not have permission to do this")
        stmt = stmt.where(User.id.in_(team_user_ids(ctx.user_id)))
    if q:
        pattern = (
            "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        stmt = stmt.where(
            or_(
                User.name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
                User.employee_code.ilike(pattern, escape="\\"),
            )
        )
    for column, value in (
        (User.status, status),
        (User.department_id, department_id),
        (User.office_id, office_id),
        (User.manager_id, manager_id),
        (User.role_key, role_key),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    return await keyset_page(
        ctx.db, stmt, [func.lower(User.name), User.id], limit=limit, cursor=cursor
    )


async def me(ctx: Ctx) -> MeOut:
    user = (await ctx.db.execute(select(User).where(User.id == ctx.user_id))).scalar_one()
    base = {
        c: getattr(user, c)
        for c in (
            "id employee_code email name phone status role_key manager_id department_id "
            "office_id last_login_at created_at"
        ).split()
    }
    office = None
    if user.office_id:
        found = (
            await ctx.db.execute(select(Office).where(Office.id == user.office_id))
        ).scalar_one_or_none()
        office = OfficeBrief.model_validate(found) if found else None
    enrolled = await get_enrollment(ctx.db, user.id) is not None
    return MeOut(
        **base,
        modules=ctx.org.modules,
        office=office,
        face_enrolled=enrolled,
        permissions=sorted(ctx.principal.permissions),
        org=OrgBrief(id=ctx.org.id, name=ctx.org.name, slug=ctx.org.slug, modules=ctx.org.modules),
        consent_required=await consent_required_for(ctx.db, ctx.org, ctx.user_id),
        consent_version=ctx.org.consent_version,
    )


# ---------------------------------------------------------------------------------- writing


async def _next_employee_code(ctx: Ctx) -> str:
    for _ in range(50):
        seq = (
            await ctx.db.execute(
                update(Organization)
                .where(Organization.id == ctx.org_id)
                .values(employee_seq=Organization.employee_seq + 1)
                .returning(Organization.employee_seq)
            )
        ).scalar_one()
        code = f"E{seq:04d}"
        taken = (await ctx.db.execute(select(User.id).where(User.employee_code == code))).first()
        if not taken:
            return code
    raise AppError(500, "code_generation_failed", "Could not allocate an employee code")


async def _validate_refs(
    ctx: Ctx,
    *,
    role_key: str | None,
    manager_id: uuid.UUID | None,
    department_id: uuid.UUID | None,
    office_id: uuid.UUID | None,
    subject_id: uuid.UUID | None = None,
) -> None:
    """Row level security already hides other orgs' rows, so 'exists' means 'in this org'."""
    if role_key is not None:
        found = (await ctx.db.execute(select(Role.key).where(Role.key == role_key))).first()
        if not found:
            raise bad_request("unknown_role", "Unknown role", role_key=role_key)
        if role_key == "owner" and not ctx.has(ORG_MANAGE):
            raise forbidden("Only an owner can grant the owner role")
    if manager_id is not None:
        if manager_id == subject_id:
            raise bad_request("invalid_manager", "A person cannot manage themselves")
        manager = (
            await ctx.db.execute(select(User).where(User.id == manager_id))
        ).scalar_one_or_none()
        if manager is None or manager.status == "disabled":
            raise bad_request("unknown_manager", "Manager not found or disabled")
        if subject_id is not None and await is_in_team(ctx.db, subject_id, manager_id):
            raise bad_request("reporting_cycle", "That would create a reporting loop")
    if department_id is not None:
        if not (
            await ctx.db.execute(select(Department.id).where(Department.id == department_id))
        ).first():
            raise bad_request("unknown_department", "Department not found")
    if office_id is not None:
        if not (await ctx.db.execute(select(Office.id).where(Office.id == office_id))).first():
            raise bad_request("unknown_office", "Office not found")


async def issue_invite(ctx: Ctx, user: User) -> None:
    """Invalidate older invites, mint a new single-use token and queue the e-mail."""
    await ctx.db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id, AuthToken.kind == "invite", AuthToken.used_at.is_(None)
        )
        .values(used_at=func.now())
    )
    token = new_token(INVITE_PREFIX)
    ctx.db.add(
        AuthToken(
            org_id=ctx.org_id,
            user_id=user.id,
            kind="invite",
            token_hash=sha256_hex(token),
            expires_at=utcnow() + timedelta(days=ctx.settings.invite_token_days),
        )
    )
    await ctx.db.flush()
    queue_invite_email(
        ctx.outbox, ctx.settings, to=user.email, name=user.name, org_name=ctx.org.name, token=token
    )


def _integrity_conflict(exc: IntegrityError) -> AppError:
    text = str(exc.orig)
    if "users_email_key" in text:
        return conflict("email_taken", "A user with this email already exists")
    if "employee_code" in text:
        return conflict("employee_code_taken", "That employee code is already in use")
    return conflict("conflict", "The change conflicts with existing data")


async def create_user(
    ctx: Ctx, data: UserCreate, *, password: str | None = None, send_invite: bool = True
) -> User:
    """Creates an ``invited`` user and mails the invite. ``password`` (seed/tests/CLI only) makes
    the user ``active`` immediately and sends nothing."""
    await _validate_refs(
        ctx,
        role_key=data.role_key,
        manager_id=data.manager_id,
        department_id=data.department_id,
        office_id=data.office_id,
    )
    await assert_seat_available(ctx.db, ctx.org)
    code = data.employee_code.strip() if data.employee_code else await _next_employee_code(ctx)
    user = User(
        id=uuid.uuid4(),
        org_id=ctx.org_id,
        employee_code=code,
        email=data.email.lower(),
        name=data.name.strip(),
        phone=data.phone,
        password_hash=await ctx.hasher.hash(password) if password else None,
        status="active" if password else "invited",
        role_key=data.role_key,
        manager_id=data.manager_id,
        department_id=data.department_id,
        office_id=data.office_id,
    )
    ctx.db.add(user)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        raise _integrity_conflict(exc) from exc
    if not password and send_invite:
        await issue_invite(ctx, user)
    await ctx.audit("people.create", "user", user.id, after=snapshot(user))
    return user


async def update_user(ctx: Ctx, user_id: uuid.UUID, data: UserUpdate) -> User:
    user = await get_user(ctx, user_id)
    if not ctx.has(PEOPLE_MANAGE):
        raise forbidden("You do not have permission to do this")
    if user.role_key == "owner" and not ctx.has(ORG_MANAGE):
        raise forbidden("Only an owner can modify an owner")
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return user
    for required in ("name", "email", "employee_code", "role_key"):
        if required in changes and changes[required] is None:
            raise bad_request("invalid_field", f"{required} cannot be empty")
    await _validate_refs(
        ctx,
        role_key=changes.get("role_key"),
        manager_id=changes.get("manager_id"),
        department_id=changes.get("department_id"),
        office_id=changes.get("office_id"),
        subject_id=user.id,
    )
    if changes.get("role_key") and changes["role_key"] != "owner" and user.role_key == "owner":
        await _assert_other_owner(ctx, user)
    before = snapshot(user)
    for key, value in changes.items():
        if key == "email":
            value = value.lower()
        setattr(user, key, value.strip() if isinstance(value, str) and key != "phone" else value)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        raise _integrity_conflict(exc) from exc
    if "role_key" in changes and changes["role_key"] != before["role_key"]:
        # Permissions are read live, but make a demoted user sign in again anyway.
        await revoke_user_sessions(ctx.db, user.id)
    await ctx.audit("people.update", "user", user.id, before, snapshot(user))
    return user


async def _assert_other_owner(ctx: Ctx, user: User) -> None:
    others = (
        await ctx.db.execute(
            select(func.count())
            .select_from(User)
            .where(User.role_key == "owner", User.status != "disabled", User.id != user.id)
        )
    ).scalar_one()
    if others == 0:
        raise conflict("last_owner", "An organization must keep at least one owner")


async def update_me(ctx: Ctx, name: str | None, phone: str | None, fields: set[str]) -> User:
    user = (await ctx.db.execute(select(User).where(User.id == ctx.user_id))).scalar_one()
    before = snapshot(user)
    if "name" in fields and name:
        user.name = name.strip()
    if "phone" in fields:
        user.phone = phone
    await ctx.db.flush()
    await ctx.audit("people.update_self", "user", user.id, before, snapshot(user))
    return user


async def disable_user(ctx: Ctx, user_id: uuid.UUID) -> User:
    user = await get_user(ctx, user_id)
    if user.id == ctx.user_id:
        raise conflict("cannot_disable_self", "You cannot disable your own account")
    if user.role_key == "owner":
        if not ctx.has(ORG_MANAGE):
            raise forbidden("Only an owner can disable an owner")
        await _assert_other_owner(ctx, user)
    if user.status == "disabled":
        return user
    before = snapshot(user)
    user.status = "disabled"
    user.disabled_at = utcnow()
    revoked = await revoke_user_sessions(ctx.db, user.id)
    # Biometric data of someone who left is deleted; an open session is closed.
    face_deleted = await delete_enrollment(ctx.db, user.id)
    await end_open_session(
        ctx.db, ctx.org_id, user.id, reason="account_disabled", actor_user_id=ctx.user_id
    )
    # Outstanding invite / reset links die with the account.
    await ctx.db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.used_at.is_(None))
        .values(used_at=func.now())
    )
    await ctx.db.flush()
    await ctx.audit(
        "people.disable",
        "user",
        user.id,
        before,
        {**snapshot(user), "sessions_revoked": revoked, "face_deleted": face_deleted},
    )
    return user


async def enable_user(ctx: Ctx, user_id: uuid.UUID) -> User:
    user = await get_user(ctx, user_id)
    if user.status != "disabled":
        return user
    if user.role_key == "owner" and not ctx.has(ORG_MANAGE):
        raise forbidden("Only an owner can enable an owner")
    await assert_seat_available(ctx.db, ctx.org)
    before = snapshot(user)
    user.status = "active" if user.password_hash else "invited"
    user.disabled_at = None
    await ctx.db.flush()
    if user.status == "invited":
        await issue_invite(ctx, user)
    await ctx.audit("people.enable", "user", user.id, before, snapshot(user))
    return user


async def resend_invite(ctx: Ctx, user_id: uuid.UUID) -> None:
    user = await get_user(ctx, user_id)
    if user.status != "invited":
        raise conflict("not_invited", "Only invited users can be sent an invite")
    await issue_invite(ctx, user)
    await ctx.audit("people.invite_resent", "user", user.id)


async def revoke_sessions_of(ctx: Ctx, user_id: uuid.UUID) -> int:
    user = await get_user(ctx, user_id)
    count = await revoke_user_sessions(ctx.db, user.id)
    await ctx.audit("people.sessions_revoked", "user", user.id, after={"count": count})
    return count


# ---------------------------------------------------------------------------- departments


async def list_departments(ctx: Ctx) -> list[Department]:
    return list(
        (await ctx.db.execute(select(Department).order_by(func.lower(Department.name)))).scalars()
    )


async def _department(ctx: Ctx, department_id: uuid.UUID) -> Department:
    dept = (
        await ctx.db.execute(select(Department).where(Department.id == department_id))
    ).scalar_one_or_none()
    if dept is None:
        raise not_found("Department")
    return dept


async def create_department(ctx: Ctx, name: str) -> Department:
    dept = Department(id=uuid.uuid4(), org_id=ctx.org_id, name=name.strip())
    ctx.db.add(dept)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        raise conflict("department_exists", "A department with this name already exists") from exc
    await ctx.audit("department.create", "department", dept.id, after={"name": dept.name})
    return dept


async def update_department(ctx: Ctx, department_id: uuid.UUID, name: str) -> Department:
    dept = await _department(ctx, department_id)
    before = {"name": dept.name}
    dept.name = name.strip()
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        raise conflict("department_exists", "A department with this name already exists") from exc
    await ctx.audit("department.update", "department", dept.id, before, {"name": dept.name})
    return dept


async def delete_department(ctx: Ctx, department_id: uuid.UUID) -> None:
    dept = await _department(ctx, department_id)
    await ctx.db.delete(dept)  # users.department_id is set to NULL by the foreign key
    await ctx.db.flush()
    await ctx.audit("department.delete", "department", department_id, before={"name": dept.name})
