# API contract v2: beacons, presence heartbeats, pluggable face providers

This is the shared design that backend, mobile and admin work is built against.
All paths are under `/api`. Auth is `Authorization: Bearer <JWT>` unless marked public.

## Why

Attendance must be hard to fake. Time only counts while **all three** hold:

1. the phone is inside the office **geofence**,
2. the employee has passed a **face check** (to start a session), and
3. the phone hears the employee's **Bluetooth beacon** (their desk) with enough signal.

Being in the building but away from your desk counts as *away*, not worked time.
The server decides everything; the app only reports what it measures.

## Beacons

Phones and laptops are not reliable Bluetooth targets (iOS hides MAC addresses,
Android randomises them, laptops stop advertising). We use standard **iBeacon**
hardware (cheap, battery-powered, fixed identifiers) registered by an admin.

Beacon document (`beacons` collection):

| field | type | notes |
|---|---|---|
| `id` | str | ObjectId as string |
| `office_id` | str | required, must exist |
| `name` | str | e.g. "Desk 14", "Floor 2 east" |
| `uuid` | str | iBeacon proximity UUID, stored lowercase with dashes |
| `major` | int \| null | 0–65535; null = match any |
| `minor` | int \| null | 0–65535; null = match any |
| `assigned_employee_id` | str \| null | null = shared zone beacon for the whole office |
| `active` | bool | default true |

Endpoints:

- `GET /beacons?office_id=` — manager/admin. Managers only see their team's offices (admins all).
- `POST /beacons` — admin. Body: all fields except `id`. 201.
- `PATCH /beacons/{id}` — admin. Partial update.
- `DELETE /beacons/{id}` — admin. 204.
- `GET /beacons/mine` — any user. The beacons the phone should scan for:
  active beacons assigned to me; if none, the active **unassigned** beacons of my office.

A reading from the phone: `BeaconReading = {uuid: str, major: int|null, minor: int|null, rssi: int}`.
A reading **matches** a beacon when uuid is equal (case-insensitive) and major/minor are equal
or the beacon's value is null. "Near" means a matching reading with `rssi >= BLUETOOTH_MIN_RSSI`
(default -80) among the beacons `/beacons/mine` would return for that user.

## Presence and sessions

Settings (env): `REQUIRE_BLUETOOTH` (default **true**), `BLUETOOTH_MIN_RSSI` (-80),
`HEARTBEAT_INTERVAL_SECONDS` (300, advice to the app), `HEARTBEAT_GRACE_MINUTES` (15).

If `REQUIRE_BLUETOOTH` is true and the user has no beacons at all (none assigned, none for the office),
check-in fails with 409 "No beacon configured for you; ask your admin".

### Check-in `POST /attendance/check-in`

Body: `{latitude, longitude, accuracy?, beacons: BeaconReading[]}`.
Requires recent face verification (unchanged), geofence (unchanged) and, if required, being near.
Creates a session with `last_heartbeat_at = started_at`, `last_state = "present"`, `present_seconds = 0`, `away_seconds = 0`.

### Heartbeat `POST /attendance/heartbeat`

Sent by the app every `HEARTBEAT_INTERVAL_SECONDS` while checked in (foreground or background).
Body: `{latitude, longitude, accuracy?, beacons: BeaconReading[]}`.

Server:
1. 409 if not checked in.
2. state = `outside` if outside geofence; else `present` if near (or bluetooth not required); else `away`.
3. Credit the interval since `last_heartbeat_at` to the **previous** `last_state`:
   `credited = min(now - last_heartbeat_at, HEARTBEAT_GRACE_MINUTES)`; add to `present_seconds`
   if previous state was `present`, else to `away_seconds`. Time beyond the grace window is not counted at all
   (phone went silent).
4. Store `last_heartbeat_at = now`, `last_state = state`, append to `presence_log` capped at the last 500 entries
   (`{at, state, distance_m, best_rssi}`).
5. If state is `outside`: close the session (`ended_at = now`).
6. Response: `{state, checked_in, session: SessionOut | null, next_heartbeat_seconds}`.

### Check-out `POST /attendance/check-out`

Finalises the open session the same way as a heartbeat (credits the last interval), then sets `ended_at`.

### Counting minutes

- `source="app"` sessions: worked = `present_seconds` plus, for an **open** session whose `last_state == "present"`,
  `min(now - last_heartbeat_at, grace)`. `SessionOut.minutes = worked // 60`.
- `source="request"` sessions (approved corrections): worked = full duration (unchanged).
- `SessionOut` gains `present_minutes`, `away_minutes`, `state` (`"present"|"away"|"outside"|null` — null for request sessions).
- Sessions still open after `MAX_SESSION_HOURS` are capped as today.

### Status `GET /attendance/status`

Adds `presence: "present"|"away"|"outside"|null` and `heartbeat_interval_seconds`.

## Face providers

`FACE_PROVIDER=opencv` (default, open source, YuNet + SFace) or `azure` (Azure AI Face; needs Microsoft
Limited Access approval for identify/verify). Both implement:

```python
class FaceProvider(Protocol):
    name: str
    def is_enrolled(self, user: dict) -> bool: ...
    async def enroll(self, user: dict, image: bytes) -> dict  # fields to $set on the user doc
    async def verify(self, user: dict, image: bytes) -> tuple[bool, float]  # (verified, score)
    async def reset(self, user: dict) -> list[str]  # user fields to $unset
```

`FaceError` (no face / several faces / unreadable) maps to 422 as today. API responses are unchanged.
