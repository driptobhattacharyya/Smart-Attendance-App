# Attendify API v1

Base path `/api/v1`. JSON in and out, timestamps ISO-8601 UTC, ids are UUIDs. OpenAPI is served at
`/openapi.json` and is the machine-readable contract. Attendance, face, requests, reports and jobs are described below.

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
Attendance and face endpoints answer `403 consent_required` otherwise. Withdrawing consent also
checks the user out (`check_out`, source `system`).

## People

| Method | Path | Permission | Body / query | Response |
|---|---|---|---|---|
| GET | `/people/me` | auth | | `user + {permissions[], org:{id,name,slug,modules}, consent_required, consent_version}` |
| PATCH | `/people/me` | auth | `{name?, phone?}` | same as GET |
| GET | `/people` | `people.manage` (whole org) or `people.view.team` (reporting tree, any depth) | `q, status, department_id, office_id, manager_id, role_key, limit, cursor` | page of `user` sorted by name |
| POST | `/people` | `people.manage` | `{name, email, employee_code?, phone?, role_key?="employee", manager_id?, department_id?, office_id?}` | `201 user` with `status: "invited"`; an invite e-mail is sent |
| GET | `/people/me/export` | auth (also after consent withdrawal) | | JSON of everything held about the caller: profile, consents, days, events, presence samples, requests, `face.enrolled` (never embeddings). Audited as `data.export` |
| GET | `/people/{id}` | auth (self, team, or `people.manage`) | | `user` |
| PATCH | `/people/{id}` | `people.manage` | any of `name, email, employee_code, phone, role_key, manager_id, department_id, office_id` (null clears the last three) | `user`. Reporting loops are rejected; only owners may touch/grant `owner`; the last owner cannot be demoted |
| POST | `/people/{id}/disable` | `people.manage` | | `user`; revokes all sessions and outstanding links, deletes the face enrolment and checks out an open session |
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

## Attendance

All need `attendance.self` and an accepted consent (`403 consent_required`). Times are ISO-8601 UTC;
`day` is a date in the user's office time zone. `Idempotency-Key` (8-200 chars) is honoured on
check-in, heartbeat and check-out: a retry returns the stored response and records nothing twice;
the same key with a different body is `422 idempotency_key_reuse`; rejections are not remembered.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/attendance/status` | | `{checked_in, session, presence, today_minutes, today, face_verified_until, face_enrolled, heartbeat_interval_seconds, wfh_allowed, requirements:{face,geofence,proximity}}` |
| GET | `/attendance/integrity-nonce` | | `{nonce, expires_in:300}`. Single use, bound to the caller; request the Play Integrity token with it |
| POST | `/attendance/check-in` | `{mode:"onsite"\|"office"\|"remote", location?, device?, beacons[]}` | `201 {session, today, integrity, best_rssi}` |
| POST | `/attendance/heartbeat` | `{location?, device?, beacons[]}` | `{checked_in, state, reason, session, next_heartbeat_seconds, best_rssi}` |
| POST | `/attendance/check-out` | `{location?}` (optional) | `{session, today}` |
| GET | `/attendance/me?start&end` | | `{start, end, days[], totals}` (default last 30 days, max 93); `day = {day, status, first_in, last_out, worked_minutes, away_minutes, break_minutes, late_minutes, early_leave_minutes, overtime_minutes, flags[], sessions[]}` |
| GET | `/attendance/policy` | | the policy in force today |
| GET | `/attendance/policies` | `policies.manage` | all versions |
| POST | `/attendance/policies` | `policies.manage`: `{effective_from, rules}` | `201`; a new version from that date (not before yesterday) |

Evidence: `location = {lat, lon, accuracy_m?, mock, captured_at?}`, `device = {platform,
app_version?, rooted, integrity_token?, integrity_nonce?}`, `beacons[] = {uuid, major, minor, rssi}`.
`mode: "onsite"` is stored and returned as `"office"`. `session = {id, mode, started_at, ended_at,
open, state, reason, last_seen_at, worked_minutes, away_minutes}`; `state` is `present|away|outside`.

Check-in rejections (all `{"error": {code, message, details}}`):

| Code | Status | Meaning |
|---|---|---|
| `consent_required`, `module_disabled` | 403 | consent missing; module off (`wfh` for remote) |
| `mock_location` | 403 | `location.mock`; audited as `attendance.rejected` |
| `location_stale`, `location_inaccurate` | 403 | fix older than `location_max_age_seconds` (120) / worse than `location_max_accuracy_m` (100) |
| `location_required` | 400 | geofence applies and no location sent |
| `outside_geofence` | 403 | `details.distance_m`, `radius_m` |
| `rooted_device` | 403 | org setting `block_rooted_devices` (default on); audited |
| `integrity_required`, `integrity_failed` | 403 | `PLAY_INTEGRITY_ENABLED`: token/nonce missing, nonce used or foreign, verdict failed; audited. iOS is `not_applicable` |
| `integrity_unavailable` | 503 | Google unreachable (fails closed) |
| `face_required` | 403 | no unconsumed `face_verified` event within `FACE_VERIFICATION_TTL_MINUTES` (10) |
| `no_office` | 409 | user has no active office (office mode) |
| `wfh_not_allowed` | 403 | remote without an approved WFH request or `wfh_weekdays` |
| `no_desk_device` | 409 | proximity required but no device for the user |
| `desk_not_near`, `desk_locked`, `desk_idle`, `desk_offline` | 403 | proximity / desk activity rules |
| `already_checked_in` | 409 | an open session exists (a lapsed one is auto-closed first) |

Heartbeat: `409 not_checked_in`; `409 session_expired` when the grace window (`heartbeat_grace_minutes`,
15) elapsed (the session was auto-closed at the last evidence, check in again). Leaving the geofence,
a mock fix or a blocked rooted device returns `state: "outside", checked_in: false` after an
`auto_check_out`. A stale or inaccurate fix is recorded but changes nothing (`reason:
"location_unreliable"`). Only state transitions become `presence_changed` events; every heartbeat
is a prunable sample. Check-out of an already-lapsed session returns `200` with it closed.

## Face

Multipart endpoints use the repeated field `frames` (JPEG, unmirrored front camera); `verify` adds
the form field `challenge_token`. Need `attendance.self`, consent and module `face`.

| Method | Path | Notes |
|---|---|---|
| GET | `/face/challenge` | `{challenge_id, action: turn_left\|turn_right, expires_in: 60, token}` (signed, bound to the user) |
| GET | `/face/status` | `{enrolled, provider, enrolled_at}` |
| POST | `/face/enroll` | 1-3 frames. `201`; `409 face_already_enrolled` (reset first); `422 face_rejected` (message tells why) |
| POST | `/face/verify` | `challenge_token` + 2-6 frames (the app sends 3). Success `200 {verified, score, liveness_score, event_id, valid_until}` and a `face_verified` event. Failure `403 face_verification_failed` with `details.reason` (`face_mismatch`, `challenge_failed`, `spoof_detected`, ...). `409 challenge_used` (single use, pass or fail), `400 invalid_challenge`/`challenge_expired`, `409 face_not_enrolled`, `429` after 10 failures in 15 minutes, `503 face_unavailable` |
| DELETE | `/face/me` | `204`; erase own enrolment |
| DELETE | `/people/{id}/face` | `people.manage`, or `people.view.team` for someone in the caller's tree (else `404`); `204`, audited `face.delete`. Disabling a user also deletes it |

Embeddings are stored encrypted (AES-GCM, `DATA_KEY`, bound to the user id); frames are never stored.

## Requests

| Method | Path | Permission | Body / query | Response |
|---|---|---|---|---|
| POST | `/requests` | `attendance.self` | `{kind: correction\|leave\|wfh, start_date, end_date?, entry_time?, exit_time?, reason}`; corrections: one day, both times, not future, <= 60 days back; WFH: today or later; modules `regularization`, `leave`, `wfh` | `201 request` |
| GET | `/requests/me` | `attendance.self` | `status, limit, cursor` | page |
| GET | `/requests` | `requests.approve.team` (reporting tree) or `.org` | `status, kind, user_id, limit, cursor` | page, newest first |
| POST | `/requests/{id}/decision` | as above | `{action: approve\|reject, comment?}` | `request`. `403 cannot_approve_own`, `404` out of scope, `409 request_not_pending` |
| POST | `/requests/{id}/cancel` | owner | | pending requests, or approved future leave/WFH |

`request = {id, user_id, user_name, kind, start_date, end_date, entry_time, exit_time, reason,
status: pending|approved|rejected|cancelled, decided_by, decided_at, comment, created_at}`. Approving a
correction appends an `interval_added` event (source `request`) and approving leave marks the days
`on_leave`, in the same transaction; both are audited (`request.create|approve|reject|cancel`).

## Corrections by administrators (`attendance.correct`)

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/attendance/corrections/intervals` | `{user_id, start, end, kind: work\|break, reason}` | `201 event` (`interval_added`, source `admin`) |
| POST | `/attendance/corrections/void` | `{event_id, reason}` | `201 event` (`event_voided`) |

A reason is mandatory; history is only appended to; nobody corrects themselves (`403
cannot_correct_own`). Audited as `attendance.interval_added|event_voided`.

## Reports (manager / admin)

Scope: `attendance.view.org` = organization, `attendance.view.team` = reporting tree; others `403`;
people outside the scope are `404`.

| Method | Path | Notes |
|---|---|---|
| GET | `/reports/presence?office_id&department_id&status` | live board; row `{user_id, name, employee_code, office_id, department_id, status: present\|away\|outside\|stale\|not_checked_in\|on_leave\|remote, reason, checked_in, session_id, mode, since, last_seen_at, best_rssi, today_minutes, face_enrolled}` |
| GET | `/reports/dashboard?date` | `{date, headcount, daily[14], late_today[10], pending_requests, setup:{employees_without_face, employees_without_desk_device, unpaired_desk_apps}}` |
| GET | `/reports/people/{user_id}/days/{day}` | self or in scope: stored and calculated day, event timeline (with `voided`), samples |
| GET | `/reports/sessions/{session_id}` | events and samples of one session |
| GET | `/reports/timesheet?start&end&office_id&department_id&limit&cursor` | `{days[], rows[{user_id, name, cells{date: {status, worked_minutes, first_in, last_out, late_minutes, flags}}, totals}], next_cursor}`, max 93 days |
| GET | `/reports/export.csv?start&end&office_id&department_id` | `reports.export`; office-local `HH:MM`; text cells starting with `= + - @` are prefixed with `'`; audited `report.export`; max 93 days / 100000 rows |

`day.status`: `present|half_day|absent|on_leave|holiday|weekend|incomplete`; flags include `late`,
`early_leave`, `overtime`, `remote`, `corrected`, `open`, `missing_check_out`, `auto_checked_out`,
`short_day`, `non_working_day`, `worked_on_leave`.

## Internal jobs

`POST /internal/jobs/{close-stale-sessions|retention|recompute}` with header
`X-Internal-Job-Secret: <INTERNAL_JOB_SECRET>` (constant-time compare; `401 invalid_job_secret`,
`503 jobs_disabled` when unset). Exempt from `X-Origin-Auth`.
`close-stale-sessions` -> `{closed, failed}`; `retention` -> `{samples_deleted, events_stripped,
challenges_deleted, keys_deleted}`; `recompute` takes `{start, end, org_id?, user_id?}` (max 93 days)
-> `{orgs, users, days}`.

Further audit actions: `attendance.rejected`, `attendance.interval_added`, `attendance.event_voided`,
`face.enroll|delete|verify_failed`, `request.create|approve|reject|cancel`, `policy.create`,
`report.export`, `data.export`.
