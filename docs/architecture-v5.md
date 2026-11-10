# Architecture v5: the sellable platform

This replaces the single-tenant MongoDB backend with a multi-tenant PostgreSQL one. API shapes
from contracts v2–v4 stay where they still make sense; breaking changes are allowed (nothing is in
production yet), and every route moves under `/api/v1`.

Guiding rules: modular monolith; business logic in services, not routes; tenant isolation enforced
by the database, not just the code; attendance stored as an immutable event log and *calculated*
by policy; every privileged change audited.

## 1. Stack

- Python 3.12, FastAPI, **SQLAlchemy 2.0 (async) + asyncpg**, **Alembic** migrations, Pydantic v2.
- PostgreSQL 16. Production: any managed Postgres reachable from AWS Lambda through a
  **transaction-mode pooler** (Supabase pooler, Neon, or RDS Proxy). Because of the pooler:
  asyncpg `statement_cache_size=0`, all tenant context set with `SET LOCAL` inside the transaction.
- No Redis/Celery yet. Scheduled jobs are HTTP endpoints under `/api/v1/internal/*` protected by a
  shared secret (`INTERNAL_JOB_SECRET`), called by an EventBridge schedule.
- Tests run against a real Postgres started by pytest (binaries at `/usr/lib/postgresql/16/bin`
  locally; `postgres:16` service container in CI). No database mocks.

Package layout (`backend/app/`):

```
core/       config, db (engine, sessions, tenant context), security (passwords, tokens), errors
auth/       login, sessions & refresh rotation, password reset, rate limiting
tenancy/    organizations, signup, modules/entitlements
rbac/       permissions, roles, dependency helpers
people/     users, departments, reporting lines
places/     offices (geofences), beacons & desk devices
attendance/ events, policy, calculation engine, presence, requests (regularization/leave/WFH)
face/       providers, liveness
audit/      audit log
api/v1/     routers only (thin)
```

## 2. Multi-tenancy

- `organizations(id uuid pk, name, slug unique, status, modules jsonb, settings jsonb, created_at)`.
- **Every tenant table has `org_id uuid not null`** and a composite index starting with `org_id`.
- A user belongs to exactly one organization (SME reality; multi-org users can come later).
- **Row Level Security** on every tenant table:
  `USING (org_id = current_setting('app.org_id')::uuid)` with `WITH CHECK` the same, and
  `ALTER TABLE … FORCE ROW LEVEL SECURITY`.
- Two database roles:
  - `attendify_app`: what request handlers use. Not the table owner, no `BYPASSRLS`.
    Every transaction starts with `SET LOCAL app.org_id = '<uuid>'` from the authenticated token.
  - `attendify_system`: `BYPASSRLS`, used **only** by a small, named set of functions:
    login lookup by email, refresh-token lookup, desk pairing/heartbeat by device token, org signup,
    internal jobs. Code review rule: nothing else imports the system session.
- Alembic runs as the owner role and creates both roles' grants and all policies.
- Tests must prove isolation at the database level: with `app.org_id` set to org A, selecting,
  updating or inserting org B rows fails or returns nothing, for every tenant table.

## 3. Identity, auth and sessions

- `users(id uuid, org_id, employee_code, email, name, phone, password_hash, status
  [active|invited|disabled], manager_id → users, department_id, office_id, created_at, …)`.
  Unique `(org_id, employee_code)` and **global unique `lower(email)`** (login is by email;
  `org_slug + employee_code` also supported).
- Passwords: argon2 (pwdlib). Login rate limit: 10 failures / 15 min per (email) and per IP,
  stored in Postgres (`auth_attempts`), then 429.
- **Access token**: JWT HS256, 15 min, claims `sub` (user id), `org`, `sid` (session id), `perms`
  version. **Refresh token**: opaque `rt_` + 32 random bytes, stored as sha256 in
  `auth_sessions(id, org_id, user_id, family_id, token_hash, device_label, created_at, last_used_at,
  expires_at (30 days), revoked_at, replaced_by)`. Every refresh **rotates** the token; presenting an
  already-rotated token revokes the whole family (theft detection).
- Endpoints: `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `POST /auth/logout-all`,
  `GET /auth/sessions` (own), `DELETE /auth/sessions/{id}`, `POST /auth/password/forgot`,
  `POST /auth/password/reset`, `POST /auth/password/change`, `POST /auth/invite/accept`.
  Email goes through an `EmailSender` interface (`console` backend in dev/tests; Resend/SES later).
- Admins can revoke any user's sessions; disabling a user revokes them.
- MFA (TOTP) for admins/owners: schema ready (`users.mfa_secret_enc`), endpoints later.

## 4. Permissions (RBAC)

Authorization checks **permissions**, never role names. Built-in roles map to permissions; stored
per org in `roles(org_id, key, name, permissions text[], builtin bool)` so orgs can customise later.
`users.role_key`.

| Permission | owner | admin | manager | employee |
|---|---|---|---|---|
| attendance.self (check in/out, own history, own requests) | ✓ | ✓ | ✓ | ✓ |
| attendance.view.team | ✓ | ✓ | ✓ | |
| attendance.view.org | ✓ | ✓ | | |
| requests.approve.team | ✓ | ✓ | ✓ | |
| requests.approve.org | ✓ | ✓ | | |
| attendance.correct (add/void events directly) | ✓ | ✓ | | |
| people.view.team | ✓ | ✓ | ✓ | |
| people.manage | ✓ | ✓ | | |
| places.manage (offices, beacons, desk devices) | ✓ | ✓ | | |
| policies.manage | ✓ | ✓ | | |
| reports.export | ✓ | ✓ | ✓ | |
| audit.view | ✓ | ✓ | | |
| org.manage (modules, billing, roles, security) | ✓ | | | |

**Team** = everyone below the user in the reporting tree (`manager_id`, recursive CTE), not the
office. A manager never approves their own requests. `owner` cannot be removed if they are the last.

## 5. Modules (entitlements)

`organizations.modules` jsonb, e.g.
`{"geofence": true, "face": true, "proximity": false, "desk_app": false, "wfh": false,
  "regularization": true, "leave": true}`.
A `require_module("face")` dependency guards routes; the attendance engine reads the same flags.
Changing modules needs `org.manage` and is audited. Plan limits (e.g. max employees) live in
`organizations.settings.plan` but billing itself is out of scope here.

## 6. Places and devices

- `offices(id, org_id, name, latitude, longitude, radius_m, timezone, address, work_start,
  work_end, active)`.
- `desk_devices(id, org_id, kind [tag|desk_app], name, office_id, assigned_user_id, active,
  ibeacon_uuid, major, minor, secret_enc, device jsonb, device_token_hash, pairing_code_hash,
  pairing_expires_at, paired_at, last_seen_at)` — replaces `beacons`.
- Tags: fixed `(uuid, major, minor)` as today.
- **Desk apps broadcast a rotating identity** (anti-cloning):
  - At pairing the server generates a 32-byte `secret` (stored encrypted with `DATA_KEY`,
    AES-GCM via `cryptography`), returned once to the desk app with the device token.
  - Broadcast window `w = floor(unix_seconds / 300)`.
    `h = HMAC-SHA256(secret, w as 8-byte big-endian)`; `major = h[0]<<8 | h[1]`;
    `minor = h[2]<<8 | h[3]`; UUID = the org's desk UUID (`organizations.settings.desk_uuid`,
    generated per org at signup).
  - Server matching: a reading with the org's desk UUID matches desk device D if `(major, minor)`
    equals D's value for `w-1`, `w` or `w+1` at the reading's server receive time.
  - `GET /places/desk-devices/mine` returns desk apps with `major/minor = null` (wildcard) so
    phones forward every reading with that UUID; tags keep exact values.
  - Desk heartbeat (`POST /desk/heartbeat`, `Authorization: Device <token>`) is unchanged apart from
    the response no longer carrying major/minor; it returns `server_time` so the desk app can
    correct clock skew (it computes windows from `unix_now + skew`).

## 7. Attendance: events, policy, calculation

### Events (immutable)
`attendance_events(id uuid, org_id, user_id, occurred_at, recorded_at, type, source
[app|desk|admin|request|system], session_id, payload jsonb, voids_event_id, actor_user_id)`.
A trigger rejects `UPDATE` and `DELETE` (except by the retention job on prunable columns — see 9).

Types: `check_in`, `check_out`, `auto_check_out` (left geofence / heartbeat timeout),
`presence_changed` (payload `{state: present|away|outside, reason}`), `face_verified`,
`remote_check_in` (WFH), `interval_added` (correction: `{start, end, kind: work|break}`),
`event_voided` (payload `{reason}`, references `voids_event_id`). Corrections never edit history:
they add `interval_added` / `event_voided` events with the actor and request id.

High-frequency heartbeats go to `presence_samples(org_id, user_id, session_id, at, state, reason,
distance_m, best_rssi, accuracy_m, mock_location, integrity)` — prunable, not the source of truth;
only **state transitions** become `presence_changed` events.

### Policy (versioned)
`attendance_policies(id, org_id, effective_from date, rules jsonb, created_by)`; the policy in
force for a day is the latest with `effective_from <= day`. Offices may override `work_start`,
`work_end`. Rules:

```json
{
  "counting": "presence",            // presence | span | span_minus_away
  "require_geofence": true,
  "require_face": true,
  "require_proximity": false,
  "desk_activity": "off",            // off | locked_only | locked_or_idle
  "desk_idle_limit_minutes": 10,
  "heartbeat_grace_minutes": 15,
  "late_after_minutes": 10,          // grace after work_start
  "early_leave_before_minutes": 15,  // before work_end
  "full_day_minutes": 480,
  "half_day_minutes": 240,
  "overtime_after_minutes": 540,
  "max_session_hours": 14
}
```
Desk activity monitoring is **off by default**; turning it on is an explicit org choice shown in
the consent text.

### Calculation engine
A pure function `compute_day(events, samples_summary, policy, office, day) -> DayRecord`:
`first_in, last_out, worked_minutes, away_minutes, break_minutes, late_minutes,
early_leave_minutes, overtime_minutes, status [present|half_day|absent|on_leave|holiday|weekend|
incomplete], flags [missing_check_out, auto_checked_out, corrected, remote, …]`.
Results are stored in `attendance_days(org_id, user_id, day, …, computed_at, policy_id)` (a
derived cache, safe to rebuild) and recomputed whenever an event for that user/day is appended.
Reports, exports and dashboards read `attendance_days`.

### Requests
`requests(id, org_id, user_id, kind [correction|leave|wfh], start_date, end_date, entry_time,
exit_time, reason, status, decided_by, decided_at, comment)`. Approval appends the corresponding
events (`interval_added` for corrections, day status for leave, an allowance for WFH) in the same
transaction, audited.

### WFH (module `wfh`)
With an approved WFH request (or policy `wfh_days`), `POST /attendance/check-in` with
`mode: "remote"` skips geofence and proximity but still requires face.

## 8. Trust signals from the phone

The server never trusts a client boolean. Check-in and heartbeats carry evidence; the server
decides and records it:
- `location {lat, lon, accuracy_m, mock: bool, captured_at}` — `mock` is Android's
  `isMock`/`isFromMockProvider`; mock → reject check-in (`403 "Mock location detected"`),
  heartbeat state `outside`. Stale (`captured_at` > 2 min old) or accuracy worse than 100 m → reject.
- `device {platform, app_version, rooted: bool, integrity_token?}` — rooted → configurable
  (`settings.block_rooted_devices`, default true). Play Integrity: when `PLAY_INTEGRITY_ENABLED`,
  the server verifies the token with Google (`decodeIntegrityToken`) and requires
  `MEETS_DEVICE_INTEGRITY` and the app's package/certificate; otherwise records "unverified".
- **Face + liveness** (server-side): `POST /face/verify` takes **3 frames** captured while the app
  shows a random challenge from the server (`GET /face/challenge` → `{challenge_id, action:
  turn_left|turn_right|blink, expires_in: 60}`). The server checks: same identity across frames,
  the challenge was performed (head yaw from YuNet landmarks / eye openness), and a passive
  anti-spoofing model (MiniFASNet, Apache-2.0, ONNX) on the frontal frame. Only embeddings and
  scores are kept; frames are discarded.

## 9. Audit, privacy, retention

- `audit_log(id, org_id, actor_user_id, actor_kind [user|system|device], action, target_type,
  target_id, before jsonb, after jsonb, ip, user_agent, at)` — append-only, written by a helper
  used in every mutating service. `GET /audit` with filters (`audit.view`).
- Consent as in contract v3, per org versioned text; `consent_required` blocks attendance & face.
- Retention job (`POST /internal/jobs/retention`): deletes `presence_samples` older than
  `settings.presence_retention_days` (90) and strips coordinates from events' payloads older than
  that (the only permitted event update, done by `attendify_system` with the trigger allowing a
  payload-only change when `app.retention_job = on`).
- Export of own data and deletion of a departed employee's biometric data (`face` removal on
  disable) are required.

## 10. API conventions

- `/api/v1/...`, JSON, ISO-8601 UTC timestamps, dates as `YYYY-MM-DD` in the office timezone.
- Errors: `{"error": {"code": "consent_required", "message": "…", "details": {}}}` with proper
  status codes; validation errors keep FastAPI's 422 but wrapped the same way.
- Cursor pagination (`?limit=&cursor=`) on list endpoints that can grow (events, audit, people).
- Idempotency: `Idempotency-Key` header supported on check-in/out and heartbeats (phones retry).
- OpenAPI is the contract for clients; generate TypeScript types for admin/mobile from it
  (`openapi-typescript`).

## 11. Delivery plan

1. **Foundation** — core, tenancy + RLS, auth + sessions, RBAC, audit, people (users, departments,
   reporting lines, invites), places (offices, desk devices incl. rotating IDs, pairing), consent,
   Alembic, Postgres test harness, demo seed.
2. **Attendance engine** — events, policies, compute_day, presence & heartbeats with trust signals,
   requests incl. WFH, dashboards/presence board/timesheets/export on `attendance_days`, retention.
3. **Face & liveness** — challenge, multi-frame verification, anti-spoof model, providers.
4. **Clients** — admin and mobile on `/api/v1` with generated types; desk app rotating IDs.
