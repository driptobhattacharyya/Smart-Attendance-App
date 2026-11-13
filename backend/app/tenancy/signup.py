"""Organization signup. Runs on the system (BYPASSRLS) session: there is no tenant yet."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..attendance.models import AttendancePolicy
from ..attendance.rules import DEFAULT_RULES, SIGNUP_POLICY_EFFECTIVE_FROM
from ..audit.service import write_audit
from ..auth import ratelimit
from ..auth.sessions import issue_session
from ..core.config import Settings
from ..core.db import Database
from ..core.errors import AppError, conflict
from ..core.security import PasswordHasher, utcnow
from ..people.models import User
from ..rbac.service import seed_builtin_roles
from .models import Organization
from .schemas import SignupIn
from .service import DEFAULT_MODULES, default_settings, slugify


async def signup_org(
    database: Database,
    settings: Settings,
    hasher: PasswordHasher,
    data: SignupIn,
    *,
    ip: str | None,
    user_agent: str | None,
):
    """Returns ``(org, owner, tokens)``."""
    if not settings.allow_signup:
        raise AppError(403, "signup_disabled", "Self-service signup is disabled")
    password_hash = await hasher.hash(data.password)
    email = data.email.lower()
    try:
        async with database.system_session() as s:
            if ip:
                await ratelimit.enforce(
                    s,
                    "signup",
                    [f"ip:{ip}"],
                    limit=settings.signup_max_per_hour,
                    window=timedelta(hours=1),
                )
                await ratelimit.record(s, "signup", f"ip:{ip}")
            exists = (
                await s.execute(select(User.id).where(func.lower(User.email) == email))
            ).first()
            if exists:
                await s.commit()  # keep the rate-limit record
                raise conflict("email_taken", "An account with this email already exists")

            base = slugify(data.org_name)
            slug = base
            while (
                await s.execute(
                    select(Organization.id).where(func.lower(Organization.slug) == slug)
                )
            ).first():
                slug = f"{base}-{secrets.token_hex(2)}"

            org = Organization(
                id=uuid.uuid4(),
                name=data.org_name.strip(),
                slug=slug,
                modules=dict(DEFAULT_MODULES),
                settings=default_settings(
                    settings.default_max_employees, settings.presence_retention_days
                ),
                employee_seq=1,
            )
            s.add(org)
            await s.flush()
            await seed_builtin_roles(s, org.id)
            owner = User(
                id=uuid.uuid4(),
                org_id=org.id,
                employee_code="E0001",
                email=email,
                name=data.name.strip(),
                phone=data.phone,
                password_hash=password_hash,
                status="active",
                role_key="owner",
                last_login_at=utcnow(),
            )
            s.add(owner)
            await s.flush()
            s.add(
                AttendancePolicy(
                    org_id=org.id,
                    effective_from=SIGNUP_POLICY_EFFECTIVE_FROM,
                    rules=DEFAULT_RULES.to_json(),
                    created_by=owner.id,
                )
            )
            tokens, _ = await issue_session(
                s,
                settings,
                org_id=org.id,
                user_id=owner.id,
                ip=ip,
                user_agent=user_agent,
                device_label="signup",
            )
            await write_audit(
                s,
                org_id=org.id,
                actor_user_id=owner.id,
                action="org.signup",
                target_type="organization",
                target_id=org.id,
                after={"name": org.name, "slug": org.slug},
                ip=ip,
                user_agent=user_agent,
            )
            return org, owner, tokens
    except IntegrityError as exc:  # lost a race on the unique e-mail / slug indexes
        raise conflict("email_taken", "An account with this email already exists") from exc
