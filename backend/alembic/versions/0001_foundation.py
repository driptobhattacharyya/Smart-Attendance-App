"""Foundation: tenancy, identity, places, audit, idempotency, roles, RLS.

Revision ID: 0001
Revises:
"""

import os

from sqlalchemy import text

from alembic import op
from app.core.rls import (
    APP_ROLE,
    SYSTEM_ROLE,
    tenant_table_statements,
    updated_at_trigger_statements,
)

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _roles() -> list[str]:
    stmts = [
        f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN NOBYPASSRLS NOSUPERUSER;
            END IF;
        END $$""",
        f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{SYSTEM_ROLE}') THEN
                CREATE ROLE {SYSTEM_ROLE} LOGIN BYPASSRLS NOSUPERUSER;
            END IF;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE WARNING 'cannot create BYPASSRLS role {SYSTEM_ROLE}: create it as a '
                'privileged user (see docs/deploy.md) and re-run';
        END $$""",
    ]
    # Optional passwords (CI and managed databases); otherwise set them yourself afterwards.
    for env, role in (
        ("ATTENDIFY_APP_PASSWORD", APP_ROLE),
        ("ATTENDIFY_SYSTEM_PASSWORD", SYSTEM_ROLE),
    ):
        if os.environ.get(env):
            stmts.append(f"ALTER ROLE {role} PASSWORD {_literal(os.environ[env])}")
    return stmts


FUNCTIONS = [
    # '' (a GUC that was SET LOCAL earlier on the connection) and unset both mean "no tenant":
    # policies then match nothing instead of raising.
    """CREATE FUNCTION app_org_id() RETURNS uuid LANGUAGE sql STABLE AS
       $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$""",
    """CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS
       $$ BEGIN NEW.updated_at = now(); RETURN NEW; END $$""",
    """CREATE FUNCTION reject_mutation() RETURNS trigger LANGUAGE plpgsql AS
       $$ BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '42501'; END $$""",
]

TABLES = [
    """CREATE TABLE organizations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name text NOT NULL,
        slug text NOT NULL,
        status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
        modules jsonb NOT NULL DEFAULT '{}',
        settings jsonb NOT NULL DEFAULT '{}',
        employee_seq integer NOT NULL DEFAULT 0,
        consent_version integer NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE UNIQUE INDEX organizations_slug_key ON organizations (lower(slug))",
    """CREATE TABLE roles (
        org_id uuid NOT NULL REFERENCES organizations (id),
        key text NOT NULL,
        name text NOT NULL,
        permissions text[] NOT NULL DEFAULT '{}',
        builtin boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id, key)
    )""",
    """CREATE TABLE departments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        name text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (org_id, id)
    )""",
    "CREATE UNIQUE INDEX departments_org_name_key ON departments (org_id, lower(name))",
    """CREATE TABLE offices (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        name text NOT NULL,
        latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
        longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
        radius_m integer NOT NULL CHECK (radius_m > 0),
        timezone text NOT NULL,
        address text,
        work_start time,
        work_end time,
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (org_id, id)
    )""",
    "CREATE INDEX offices_org_idx ON offices (org_id, name)",
    """CREATE TABLE users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        employee_code text NOT NULL,
        email text NOT NULL,
        name text NOT NULL,
        phone text,
        password_hash text,
        status text NOT NULL CHECK (status IN ('active', 'invited', 'disabled')),
        role_key text NOT NULL,
        manager_id uuid,
        department_id uuid,
        office_id uuid,
        mfa_secret_enc bytea,
        last_login_at timestamptz,
        disabled_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (org_id, id),
        UNIQUE (org_id, employee_code),
        FOREIGN KEY (org_id, role_key) REFERENCES roles (org_id, key),
        FOREIGN KEY (org_id, manager_id) REFERENCES users (org_id, id),
        FOREIGN KEY (org_id, department_id) REFERENCES departments (org_id, id)
            ON DELETE SET NULL (department_id),
        FOREIGN KEY (org_id, office_id) REFERENCES offices (org_id, id)
            ON DELETE SET NULL (office_id),
        CHECK (manager_id IS NULL OR manager_id <> id)
    )""",
    "CREATE UNIQUE INDEX users_email_key ON users (lower(email))",
    "CREATE INDEX users_org_name_idx ON users (org_id, lower(name), id)",
    "CREATE INDEX users_org_manager_idx ON users (org_id, manager_id)",
    """CREATE TABLE desk_devices (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        kind text NOT NULL CHECK (kind IN ('tag', 'desk_app')),
        name text NOT NULL,
        office_id uuid,
        assigned_user_id uuid,
        active boolean NOT NULL DEFAULT true,
        ibeacon_uuid uuid,
        major integer CHECK (major BETWEEN 0 AND 65535),
        minor integer CHECK (minor BETWEEN 0 AND 65535),
        secret_enc bytea,
        device jsonb NOT NULL DEFAULT '{}',
        device_token_hash text,
        pairing_code_hash text,
        pairing_expires_at timestamptz,
        paired_at timestamptz,
        last_seen_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (org_id, id),
        FOREIGN KEY (org_id, office_id) REFERENCES offices (org_id, id)
            ON DELETE SET NULL (office_id),
        FOREIGN KEY (org_id, assigned_user_id) REFERENCES users (org_id, id)
            ON DELETE SET NULL (assigned_user_id),
        CHECK (kind <> 'tag' OR (ibeacon_uuid IS NOT NULL AND major IS NOT NULL
               AND minor IS NOT NULL))
    )""",
    "CREATE UNIQUE INDEX desk_devices_tag_key ON desk_devices (org_id, ibeacon_uuid, major, minor) "
    "WHERE kind = 'tag'",
    "CREATE UNIQUE INDEX desk_devices_token_key ON desk_devices (device_token_hash) "
    "WHERE device_token_hash IS NOT NULL",
    "CREATE UNIQUE INDEX desk_devices_pairing_key ON desk_devices (pairing_code_hash) "
    "WHERE pairing_code_hash IS NOT NULL",
    "CREATE INDEX desk_devices_org_idx ON desk_devices (org_id, kind, name)",
    """CREATE TABLE auth_sessions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        user_id uuid NOT NULL,
        family_id uuid NOT NULL,
        token_hash text NOT NULL,
        device_label text,
        ip text,
        user_agent text,
        created_at timestamptz NOT NULL DEFAULT now(),
        family_created_at timestamptz NOT NULL DEFAULT now(),
        last_used_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        revoked_at timestamptz,
        replaced_by uuid,
        FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id)
    )""",
    "CREATE UNIQUE INDEX auth_sessions_token_key ON auth_sessions (token_hash)",
    "CREATE INDEX auth_sessions_family_idx ON auth_sessions (org_id, family_id)",
    "CREATE INDEX auth_sessions_user_idx ON auth_sessions (org_id, user_id)",
    # Not tenant data: the rate-limit ledger is keyed by e-mail / IP before anyone is known.
    """CREATE TABLE auth_attempts (
        id bigserial PRIMARY KEY,
        kind text NOT NULL,
        key text NOT NULL,
        success boolean NOT NULL DEFAULT false,
        at timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX auth_attempts_lookup_idx ON auth_attempts (kind, key, at)",
    """CREATE TABLE auth_tokens (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        user_id uuid NOT NULL,
        kind text NOT NULL CHECK (kind IN ('reset', 'invite')),
        token_hash text NOT NULL,
        expires_at timestamptz NOT NULL,
        used_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id)
    )""",
    "CREATE UNIQUE INDEX auth_tokens_hash_key ON auth_tokens (token_hash)",
    "CREATE INDEX auth_tokens_user_idx ON auth_tokens (org_id, user_id, kind)",
    """CREATE TABLE consents (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        user_id uuid NOT NULL,
        version integer NOT NULL,
        accepted_at timestamptz NOT NULL DEFAULT now(),
        withdrawn_at timestamptz,
        ip text,
        FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id)
    )""",
    "CREATE UNIQUE INDEX consents_active_key ON consents (user_id, version) "
    "WHERE withdrawn_at IS NULL",
    "CREATE INDEX consents_org_idx ON consents (org_id, user_id)",
    """CREATE TABLE audit_log (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        actor_user_id uuid,
        actor_kind text NOT NULL CHECK (actor_kind IN ('user', 'system', 'device')),
        action text NOT NULL,
        target_type text,
        target_id text,
        before jsonb,
        after jsonb,
        ip text,
        user_agent text,
        at timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX audit_log_org_at_idx ON audit_log (org_id, at DESC, id DESC)",
    "CREATE INDEX audit_log_target_idx ON audit_log (org_id, target_type, target_id)",
    """CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
       FOR EACH ROW EXECUTE FUNCTION reject_mutation()""",
    """CREATE TABLE idempotency_keys (
        org_id uuid NOT NULL REFERENCES organizations (id),
        key text NOT NULL,
        user_id uuid,
        scope text NOT NULL,
        request_hash text NOT NULL,
        response_status integer,
        response_body jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id, key)
    )""",
    "CREATE INDEX idempotency_keys_created_idx ON idempotency_keys (created_at)",
]

# (table, org column, privileges the request-handling role gets)
TENANT_TABLES = [
    ("organizations", "id", "SELECT, UPDATE"),
    ("roles", "org_id", None),
    ("departments", "org_id", None),
    ("offices", "org_id", None),
    ("users", "org_id", None),
    ("desk_devices", "org_id", None),
    ("auth_sessions", "org_id", None),
    ("auth_tokens", "org_id", None),
    ("consents", "org_id", None),
    ("audit_log", "org_id", "SELECT, INSERT"),
    ("idempotency_keys", "org_id", None),
]

UPDATED_AT_TABLES = ["organizations", "departments", "offices", "users", "desk_devices"]


def upgrade() -> None:
    statements: list[str] = [*_roles()]
    statements += [
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}, {SYSTEM_ROLE}",
        *FUNCTIONS,
        *TABLES,
    ]
    for table, org_col, privileges in TENANT_TABLES:
        kwargs = {"org_col": org_col}
        if privileges:
            kwargs["app_privileges"] = privileges
        statements += tenant_table_statements(table, **kwargs)
    for table in UPDATED_AT_TABLES:
        statements += updated_at_trigger_statements(table)
    statements += [
        f"GRANT ALL ON auth_attempts TO {SYSTEM_ROLE}",
        f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {SYSTEM_ROLE}",
        f"GRANT EXECUTE ON FUNCTION app_org_id() TO {APP_ROLE}, {SYSTEM_ROLE}",
    ]
    for statement in statements:
        op.execute(text(statement))


def downgrade() -> None:
    for table in [
        "idempotency_keys",
        "audit_log",
        "consents",
        "auth_tokens",
        "auth_attempts",
        "auth_sessions",
        "desk_devices",
        "users",
        "offices",
        "departments",
        "roles",
        "organizations",
    ]:
        op.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    for function in ("reject_mutation()", "set_updated_at()", "app_org_id()"):
        op.execute(text(f"DROP FUNCTION IF EXISTS {function}"))
