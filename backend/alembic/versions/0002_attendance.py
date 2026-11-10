"""Attendance engine: events, presence samples, policies, derived days, requests, face data.

Revision ID: 0002
Revises: 0001
"""

from sqlalchemy import text

from alembic import op
from app.core.rls import (
    APP_ROLE,
    SYSTEM_ROLE,
    tenant_table_statements,
    updated_at_trigger_statements,
)

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

FUNCTIONS = [
    # Append-only event log. The single permitted UPDATE: the retention job (system role, with
    # app.retention_job = 'on') may change ``payload`` and nothing else. DELETE never.
    """CREATE FUNCTION attendance_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
         IF TG_OP = 'UPDATE'
            AND current_setting('app.retention_job', true) = 'on'
            AND current_user = 'attendify_system'
            AND (to_jsonb(NEW) - 'payload') = (to_jsonb(OLD) - 'payload') THEN
           RETURN NEW;
         END IF;
         RAISE EXCEPTION 'attendance_events is append-only' USING ERRCODE = '42501';
       END $$""",
]

TABLES = [
    """CREATE TABLE attendance_policies (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        effective_from date NOT NULL,
        rules jsonb NOT NULL,
        created_by uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (org_id, id)
    )""",
    "CREATE INDEX attendance_policies_effective_idx ON attendance_policies "
    "(org_id, effective_from DESC, created_at DESC)",
    """CREATE TABLE requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        user_id uuid NOT NULL,
        kind text NOT NULL CHECK (kind IN ('correction', 'leave', 'wfh')),
        start_date date NOT NULL,
        end_date date NOT NULL,
        entry_time time,
        exit_time time,
        reason text NOT NULL,
        status text NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
        decided_by uuid,
        decided_at timestamptz,
        comment text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (org_id, id),
        FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id),
        CHECK (end_date >= start_date),
        CHECK (kind <> 'correction' OR (entry_time IS NOT NULL AND exit_time IS NOT NULL
               AND start_date = end_date))
    )""",
    "CREATE INDEX requests_user_idx ON requests (org_id, user_id, created_at DESC)",
    "CREATE INDEX requests_status_idx ON requests (org_id, status, created_at DESC)",
    "CREATE INDEX requests_dates_idx ON requests "
    "(org_id, user_id, kind, status, start_date, end_date)",
    """CREATE TABLE attendance_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        user_id uuid NOT NULL,
        occurred_at timestamptz NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        type text NOT NULL CHECK (type IN ('check_in', 'check_out', 'auto_check_out',
            'presence_changed', 'face_verified', 'remote_check_in', 'interval_added',
            'event_voided')),
        source text NOT NULL CHECK (source IN ('app', 'desk', 'admin', 'request', 'system')),
        session_id uuid,
        day date NOT NULL,
        payload jsonb NOT NULL DEFAULT '{}',
        voids_event_id uuid,
        actor_user_id uuid,
        request_id uuid,
        UNIQUE (org_id, id),
        FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id),
        FOREIGN KEY (org_id, voids_event_id) REFERENCES attendance_events (org_id, id),
        FOREIGN KEY (org_id, session_id) REFERENCES attendance_events (org_id, id),
        FOREIGN KEY (org_id, request_id) REFERENCES requests (org_id, id),
        CHECK ((type = 'event_voided') = (voids_event_id IS NOT NULL))
    )""",
    "CREATE INDEX attendance_events_day_idx ON attendance_events "
    "(org_id, user_id, day, occurred_at)",
    "CREATE INDEX attendance_events_user_idx ON attendance_events "
    "(org_id, user_id, occurred_at DESC)",
    "CREATE INDEX attendance_events_session_idx ON attendance_events (org_id, session_id) "
    "WHERE session_id IS NOT NULL",
    "CREATE INDEX attendance_events_voids_idx ON attendance_events (org_id, voids_event_id) "
    "WHERE voids_event_id IS NOT NULL",
    "CREATE INDEX attendance_events_type_idx ON attendance_events (org_id, type, occurred_at DESC)",
    "CREATE INDEX attendance_events_face_idx ON attendance_events ((payload ->> 'face_event_id')) "
    "WHERE payload ? 'face_event_id'",
    """CREATE TRIGGER attendance_events_append_only BEFORE UPDATE OR DELETE ON attendance_events
       FOR EACH ROW EXECUTE FUNCTION attendance_events_guard()""",
    """CREATE TABLE presence_samples (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        user_id uuid NOT NULL,
        session_id uuid NOT NULL,
        at timestamptz NOT NULL,
        state text NOT NULL CHECK (state IN ('present', 'away', 'outside')),
        reason text,
        distance_m real,
        best_rssi integer,
        accuracy_m real,
        mock_location boolean NOT NULL DEFAULT false,
        integrity text,
        FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id),
        FOREIGN KEY (org_id, session_id) REFERENCES attendance_events (org_id, id)
    )""",
    "CREATE INDEX presence_samples_session_idx ON presence_samples (org_id, session_id, at DESC)",
    "CREATE INDEX presence_samples_at_idx ON presence_samples (at)",
    """CREATE TABLE attendance_days (
        org_id uuid NOT NULL REFERENCES organizations (id),
        user_id uuid NOT NULL,
        day date NOT NULL,
        first_in timestamptz,
        last_out timestamptz,
        worked_minutes integer NOT NULL DEFAULT 0,
        away_minutes integer NOT NULL DEFAULT 0,
        break_minutes integer NOT NULL DEFAULT 0,
        late_minutes integer NOT NULL DEFAULT 0,
        early_leave_minutes integer NOT NULL DEFAULT 0,
        overtime_minutes integer NOT NULL DEFAULT 0,
        status text NOT NULL CHECK (status IN ('present', 'half_day', 'absent', 'on_leave',
            'holiday', 'weekend', 'incomplete')),
        flags jsonb NOT NULL DEFAULT '[]',
        sessions jsonb NOT NULL DEFAULT '[]',
        office_id uuid,
        policy_id uuid,
        computed_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id, user_id, day),
        FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id)
    )""",
    "CREATE INDEX attendance_days_day_idx ON attendance_days (org_id, day, status)",
    """CREATE TABLE face_enrollments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations (id),
        user_id uuid NOT NULL,
        provider text NOT NULL,
        model text NOT NULL DEFAULT '',
        data_enc bytea NOT NULL,
        frames_used integer NOT NULL DEFAULT 1,
        enrolled_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (org_id, user_id),
        FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id)
    )""",
    """CREATE TABLE face_challenges_used (
        org_id uuid NOT NULL REFERENCES organizations (id),
        challenge_id text NOT NULL,
        user_id uuid NOT NULL,
        used_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        PRIMARY KEY (org_id, challenge_id),
        FOREIGN KEY (org_id, user_id) REFERENCES users (org_id, id)
    )""",
    "CREATE INDEX face_challenges_used_expires_idx ON face_challenges_used (expires_at)",
]

# (table, privileges for the request-handling role; None = read/write)
TENANT_TABLES = [
    ("attendance_policies", "SELECT, INSERT"),
    ("requests", None),
    ("attendance_events", "SELECT, INSERT"),  # append-only for the app role
    ("presence_samples", "SELECT, INSERT"),  # pruned by the retention job (system role)
    ("attendance_days", None),
    ("face_enrollments", None),
    ("face_challenges_used", "SELECT, INSERT"),  # pruned by the retention job (system role)
]


def upgrade() -> None:
    statements: list[str] = [*FUNCTIONS, *TABLES]
    for table, privileges in TENANT_TABLES:
        kwargs = {"app_privileges": privileges} if privileges else {}
        statements += tenant_table_statements(table, **kwargs)
    statements += updated_at_trigger_statements("requests")
    statements += [
        f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {SYSTEM_ROLE}",
        f"GRANT EXECUTE ON FUNCTION attendance_events_guard() TO {APP_ROLE}, {SYSTEM_ROLE}",
    ]
    for statement in statements:
        op.execute(text(statement))


def downgrade() -> None:
    for table in [
        "face_challenges_used",
        "face_enrollments",
        "attendance_days",
        "presence_samples",
        "attendance_events",
        "requests",
        "attendance_policies",
    ]:
        op.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    op.execute(text("DROP FUNCTION IF EXISTS attendance_events_guard()"))
