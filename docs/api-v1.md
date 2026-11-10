# Attendify API v1 (foundation)

Base path `/api/v1`. JSON in and out, timestamps ISO-8601 UTC, ids are UUIDs. OpenAPI is served at
`/openapi.json` and is the machine-readable contract. Attendance, requests, reports and face
endpoints arrive in later delivery steps.

## Conventions

- **Auth**: `Authorization: Bearer <access_token>` (JWT, 15 min). Refresh with the opaque `rt_...`
  token (30 days, rotated on every use). Desk apps use `Authorization: Device <device_token>`.
- **Errors**: `{"error": {"code": "...", "message": "...", "details": {}}}`. Validation errors are
  `422` with `code: "validation_error"` and `details.errors[] = {loc, msg, type}`. Common codes:
  `unauthorized`, `invalid_token`, `session_revoked`, `invalid_credentials`, `account_disabled`,
  `forbidden`, `module_disabled`, `consent_required`, `not_found`, `conflict` family
  (`email_taken`, `employee_code_taken`, `last_owner`, ...), `plan_limit_reached`, `rate_limited`
  (429 with `Retry-After`), `token_reuse_detected`, `invalid_cursor`.
- **Pagination**: `?limit=` (1-200, default 50) and `?cursor=`; response `{"items": [...],
  "next_cursor": "..."|null`}. Cursors are opaque.
- **Permissions** are checked, never role names. Out-of-scope people read as `404`.
- No response contains password hashes, token hashes or secrets. The only exception is the
  one-time `secret` in `POST /desk/pair`.

Built-in roles: `owner` (everything), `admin` (everything except `org.manage`), `manager`
(`attendance.self`, `attendance.view.team`, `requests.approve.team`, `people.view.team`,
`reports.export`), `employee` (`attendance.self`).

Notation: "auth" = any signed-in user; otherwise the required permission.

## Health

| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/health` | none | `{"status":"ok","database":"up"}`, `503` when the DB is down |

## Organizations

| Method | Path | Permission | Body | Response |
|---|---|---|---|---|
| POST | `/orgs/signup` | none (rate limited per IP) | `{org_name, name, email, password(10-128), phone?}` | `201 {access_token, refresh_token, token_type, expires_in, org, user, consent_required}`; `org = {id,name,slug,status,modules,settings,consent_version,created_at}` |
| GET | `/org` | `org.manage` | | `org` |
| PATCH | `/org` | `org.manage` | `{name?, modules?: {geofence,face,proximity,desk_app,wfh,regularization,leave: bool}, settings?: {block_rooted_devices, presence_retention_days, desk_activity_monitoring}, bump_consent_version?: bool}` | `org` |

`org.settings.desk_uuid` is the org's iBeacon UUID for desk apps; `settings.plan.max_employees` is
the seat limit (invited and active users count; creating or enabling beyond it is `403
plan_limit_reached`).

## Auth

| Method | Path | Auth | Body | Response |
|---|---|---|---|---|
| POST | `/auth/login` | none | `{email, password, device_label?}` or `{org_slug, employee_code, password, device_label?}` | `{access_token, refresh_token, token_type, expires_in, user, consent_required}` |
| POST | `/auth/refresh` | none | `{refresh_token}` | `{access_token, refresh_token, token_type, expires_in}`. Reusing a rotated token revokes the whole session and returns `401 token_reuse_detected` |
| POST | `/auth/logout` | none | `{refresh_token}` | `204` (idempotent) |
| POST | `/auth/logout-all` | auth | | `204` |
| GET | `/auth/sessions` | auth | | `[{id, device_label, ip, user_agent, created_at, last_used_at, expires_at, current}]` |
| DELETE | `/auth/sessions/{id}` | auth (own) | | `204` |
| POST | `/auth/password/forgot` | none | `{email}` | `202 {message}` (identical for unknown e-mails; 5 per hour per e-mail/IP) |
| POST | `/auth/password/reset` | none | `{token, new_password}` | `{message}`. Single use, 60 min, revokes all sessions |
| POST | `/auth/password/change` | auth | `{current_password, new_password}` | `{message}`; other sessions are revoked |
| POST | `/auth/invite/accept` | none | `{token, password}` | same as login. Single use, 7 days |
| GET | `/consent/text` | auth | | `{version, title, body_markdown, accepted}` for the caller's org |
| POST | `/auth/consent` | auth | `{version}` | `{message}`; `409 consent_version_mismatch` unless it is the org's current version |
| POST | `/auth/consent/withdraw` | auth | | `{message}` |

Login is rate limited to 10 failures per 15 minutes per e-mail (or org+code) and per IP, then `429`.
`consent_required` is true until the user accepts the org's current consent version.
Attendance and face endpoints (later steps) answer `403 consent_required` otherwise.

## People

| Method | Path | Permission | Body / query | Response |
|---|---|---|---|---|
| GET | `/people/me` | auth | | `user + {permissions[], org:{id,name,slug,modules}, consent_required, consent_version}` |
| PATCH | `/people/me` | auth | `{name?, phone?}` | same as GET |
| GET | `/people` | `people.manage` (whole org) or `people.view.team` (reporting tree, any depth) | `q, status, department_id, office_id, manager_id, role_key, limit, cursor` | page of `user` sorted by name |
| POST | `/people` | `people.manage` | `{name, email, employee_code?, phone?, role_key?="employee", manager_id?, department_id?, office_id?}` | `201 user` with `status: "invited"`; an invite e-mail is sent |
| GET | `/people/{id}` | auth (self, team, or `people.manage`) | | `user` |
| PATCH | `/people/{id}` | `people.manage` | any of `name, email, employee_code, phone, role_key, manager_id, department_id, office_id` (null clears the last three) | `user`. Reporting loops are rejected; only owners may touch/grant `owner`; the last owner cannot be demoted |
| POST | `/people/{id}/disable` | `people.manage` | | `user`; revokes all sessions and outstanding links |
| POST | `/people/{id}/enable` | `people.manage` | | `user` (seat limit applies) |
| POST | `/people/{id}/invite` | `people.manage` | | `{message}`; invalidates earlier invite links |
| GET | `/people/{id}/sessions` | `people.manage` | | sessions |
| POST | `/people/{id}/revoke-sessions` | `people.manage` | | `{message}` |
| GET | `/departments` | auth | | `[{id, name, created_at}]` |
| POST | `/departments` | `people.manage` | `{name}` | `201 department` |
| PATCH | `/departments/{id}` | `people.manage` | `{name}` | department |
| DELETE | `/departments/{id}` | `people.manage` | | `204`; members keep no department |

`user = {id, employee_code, email, name, phone, status: active|invited|disabled, role_key,
manager_id, department_id, office_id, last_login_at, created_at}`.

## Places

| Method | Path | Permission | Body | Response |
|---|---|---|---|---|
| GET | `/places/offices` | auth | | `[office]` |
| GET | `/places/offices/{id}` | auth | | office |
| POST | `/places/offices` | `places.manage` | `{name, latitude, longitude, radius_m?=150, timezone?="UTC", address?, work_start?, work_end?, active?}` | `201 office` |
| PATCH | `/places/offices/{id}` | `places.manage` | any office field | office |
| DELETE | `/places/offices/{id}` | `places.manage` | | `204`; `409 office_in_use` if referenced (deactivate instead) |
| GET | `/places/desk-devices/mine` | auth | | devices the phone should scan for (see below) |
| GET | `/places/desk-devices` | `places.manage` | `?kind=tag\|desk_app` | `[device]` |
| POST | `/places/desk-devices` | `places.manage` | tag: `{kind:"tag", name, ibeacon_uuid, major, minor, office_id?, assigned_user_id?}`; desk app: `{kind:"desk_app", name, office_id?, assigned_user_id?}` (needs module `desk_app`) | `201 device` |
| GET / PATCH / DELETE | `/places/desk-devices/{id}` | `places.manage` | PATCH: `name, office_id, assigned_user_id, active`, tags also `ibeacon_uuid, major, minor` | device / `204` |
| POST | `/places/desk-devices/{id}/pairing-code` | `places.manage` | | `{code:"ABCD-EFGH", expires_at}` (15 min, single use) |
| POST | `/places/desk-devices/{id}/unpair` | `places.manage` | | device; the device token and secret stop working |

`office = {id, name, latitude, longitude, radius_m, timezone, address, work_start, work_end, active}`.
`device = {id, kind, name, office_id, assigned_user_id, active, ibeacon_uuid, major, minor, paired,
paired_at, last_seen_at, device}` where `device` is the last reported desk state `{hostname, os,
agent_version, locked, idle_seconds, advertising}`. For desk apps `ibeacon_uuid` is the org's desk
UUID and `major`/`minor` are always `null` (they rotate; see architecture appendix A).

`/places/desk-devices/mine` returns active tags (exact identity) and paired desk apps (wildcard
`major`/`minor` = null with the org desk UUID), excluding devices assigned to other people.

## Desk app (no user login)

| Method | Path | Auth | Body | Response |
|---|---|---|---|---|
| POST | `/desk/pair` | none (10 failed attempts / 15 min per IP) | `{code, hostname?, os?, agent_version?}` | `{device_token, secret (base64, 32 bytes), beacon:{id,name,uuid,window_seconds:300}, employee:{id,employee_code,name}\|null, heartbeat_interval_seconds, server_time}`. Re-pairing replaces token and secret. `secret` is never returned again |
| POST | `/desk/heartbeat` | `Authorization: Device <token>` | `{locked?, idle_seconds?, advertising?, agent_version?}` | `{server_time, heartbeat_interval_seconds, paired:true}`; `401 device_not_paired` when unpaired |

## Audit

| Method | Path | Permission | Query | Response |
|---|---|---|---|---|
| GET | `/audit` | `audit.view` | `actor_user_id, action (exact, or a prefix ending in "."), target_type, target_id, since, until, limit, cursor` | page of `{id, actor_user_id, actor_kind, action, target_type, target_id, before, after, ip, at}`, newest first |

Actions: `org.signup`, `org.update`, `auth.login`, `auth.logout`, `auth.logout_all`,
`auth.session_revoked`, `auth.password_changed`, `auth.password_reset_requested`,
`auth.password_reset`, `auth.invite_accepted`, `auth.refresh_reuse_detected`, `consent.accept`,
`consent.withdraw`, `people.create|update|update_self|disable|enable|invite_resent|sessions_revoked`,
`department.create|update|delete`, `office.create|update|delete`,
`device.create|update|delete|pairing_code|unpair|paired`.
