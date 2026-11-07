# API contract v3: endpoints for the admin console

Adds read endpoints the admin console needs. Everything in v2 stays. All under `/api`,
Bearer auth, **manager or admin** role. "Team scope" = admins see everyone, managers see users
with their `team_id` (same rule as the existing `/manager/*` routes).

## Live presence board — `GET /manager/presence`

Query: `office_id?`. One row per active team member:

```json
{
  "employee_id": "EMP0003",
  "name": "Ananya Sen",
  "office_id": "…",
  "status": "present",            // present | away | outside | stale | not_checked_in | on_leave
  "checked_in": true,
  "session_id": "…",              // open session or null
  "since": "2026-11-07T03:35:00Z",// started_at of the open session, null otherwise
  "last_heartbeat_at": "…",       // null if not checked in
  "best_rssi": -61,               // from the latest presence_log entry, null if unknown
  "today_minutes": 312,           // worked (present) minutes today, office timezone
  "face_enrolled": true,
  "has_beacon": true              // /beacons/mine would return at least one beacon
}
```

`stale` = checked in but no heartbeat for longer than `HEARTBEAT_GRACE_MINUTES` (phone went silent).
`on_leave` = not checked in and an approved leave request covers today.
Sorted: present, away, stale, outside, not_checked_in, on_leave; then by name.

## Dashboard — `GET /manager/dashboard`

Query: `date?` (defaults to today in the first office's timezone; use UTC if none).

```json
{
  "date": "2026-11-07",
  "headcount": {"total": 24, "present": 15, "away": 3, "stale": 1, "outside": 0,
                 "not_checked_in": 4, "on_leave": 1},
  "pending_requests": 3,
  "unassigned_beacons": 2,                  // active beacons with no employee (admins only; managers get null)
  "employees_without_face": 1,
  "employees_without_beacon": 2,            // when REQUIRE_BLUETOOTH is on
  "daily": [                                 // last 14 days ending on `date`, oldest first
    {"date": "2026-10-25", "worked_minutes": 6120, "away_minutes": 410, "people": 21}
  ],
  "late_today": [                            // first check-in after office start (see below), max 10
    {"employee_id": "EMP0007", "name": "…", "first_check_in": "…"}
  ]
}
```

Offices gain an optional `work_start` field (`"HH:MM"`, office local time, default `"09:30"`) used for "late".
`OfficeIn`/`OfficeUpdate`/`Office` accept and return it.

## Session detail with timeline — `GET /manager/sessions/{session_id}`

Team scope (404 otherwise). Returns `SessionOut` fields plus:

```json
{
  "employee_id": "EMP0003", "name": "Ananya Sen", "office_id": "…",
  "check_in": {"latitude": 0, "longitude": 0, "distance_m": 12.3, "best_rssi": -60},
  "presence_log": [{"at": "…", "state": "present", "distance_m": 10.1, "best_rssi": -64}]
}
```

## Day detail for one employee — existing `GET /manager/attendance/{employee_id}`

Unchanged; each day's `sessions[]` already carries `id`, used to fetch the timeline above.

## CSV export — `GET /manager/attendance/export.csv`

Query: `start`, `end` (max 93 days, same validation as reports), `office_id?`.
`Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="attendance_<start>_<end>.csv"`.
One row per employee per day that has sessions or leave:

`employee_id,name,office,date,first_in,last_out,worked_minutes,away_minutes,sessions,on_leave`

Times are office-local `HH:MM`. Escape with the `csv` module. Must be registered **before**
`/manager/attendance/{employee_id}` so the path isn't captured by it.

## Request decision comment

Already exists (`DecisionIn.comment`); the console shows it.

## Consent and data retention (DPDP Act / Google Play "prominent disclosure")

Setting `CONSENT_VERSION` (int, default `1`). Users store `consent: {version: int, accepted_at: datetime}`.

- `UserOut` and the login/register `AuthOut` gain `consent_required: bool`
  (true when the user has no consent or an older version).
- `POST /auth/consent` — body `{version: int}`; 409 if `version != CONSENT_VERSION`;
  stores consent and returns `UserOut`.
- `POST /auth/consent/withdraw` — clears consent; also checks the user out if checked in.
- **Check-in, heartbeat, face enrol and face verify** return **403** `{"detail": "Consent required"}`
  while `consent_required` is true.
- `GET /auth/me/export` — JSON with the user's profile (no password hash, no face embedding values,
  only `face_enrolled`), sessions (with presence logs), and requests. Right of access.
- Retention: setting `PRESENCE_RETENTION_DAYS` (default 90). Presence logs (`presence_log`,
  `check_in`/`check_out` coordinates) of sessions whose `date` is older than that are removed
  (`$unset`), keeping the worked/away totals. Runs (a) opportunistically at most once per hour per
  process, triggered on check-in, and (b) on demand via `POST /admin/maintenance/prune` (admin) which
  returns `{sessions_pruned: int}`.
- `GET /consent/text` — public; `{version, title, body_markdown}` with the disclosure the app shows
  (what is collected: location only while checked in, beacon signal strength, a face embedding not
  the photo, timestamps; why; who sees it — the employee's managers and admins; retention 90 days;
  how to withdraw). Text lives in `backend/app/consent.md` with `{retention_days}` substituted.
