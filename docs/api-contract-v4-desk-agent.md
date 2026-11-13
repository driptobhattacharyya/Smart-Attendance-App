# API contract v4: the Attendify desk app (office computer as the beacon)

Instead of (or as well as) a stick-on Bluetooth tag, an employee's office computer runs the
**Attendify Desk** app. It broadcasts a standard **iBeacon** advertisement over Bluetooth LE, so the
mobile app and the server treat it exactly like a tag (no mobile changes). It also reports whether
the computer is **unlocked and in use**, which makes "at the desk and working" measurable.

## Beacon kinds

`beacons` documents gain:

| field | type | notes |
|---|---|---|
| `kind` | `"tag"` \| `"desk_app"` | default `"tag"` (existing beacons) |
| `device` | object \| null | desk app only: `{hostname, os, agent_version, paired_at, last_seen_at, locked, idle_seconds, advertising}` |
| `device_token_hash` | str \| null | sha256 of the device token; never returned by the API |
| `pairing` | object \| null | `{code_hash, expires_at}` while a pairing code is outstanding; never returned |

`Beacon` responses gain `kind`, `device` (as above, or null), `paired: bool`, `online: bool`
(`device.last_seen_at` within `DESK_HEARTBEAT_GRACE_SECONDS`, default 180).

Creating a `desk_app` beacon (`POST /beacons` with `kind: "desk_app"`): `uuid` is optional and
defaults to the organisation UUID setting `DESK_BEACON_UUID` (default
`6a1f3c2e-8b4d-4e7a-9c2f-a7d1e5b3c901`); `major`/`minor` are optional and, if omitted, the server
allocates a unique `(major, minor)` pair for that UUID (major = 1..65535, minor = 1..65535, never
reused while the beacon exists).

## Pairing

1. Admin: `POST /beacons/{id}/pairing-code` → `{code: "ABCD-2345", expires_at}`. 8 characters from
   an unambiguous alphabet (no 0/O/1/I/L), shown once, valid `DESK_PAIRING_TTL_MINUTES` (15), stored
   hashed. Only for `kind == "desk_app"`. Creating a new code invalidates any previous one.
2. Desk app (public, rate-limit by being cheap and single-use): `POST /desk/pair`
   body `{code, hostname, os, agent_version}` → `{device_token, beacon: {id, name, uuid, major, minor},
   employee: {employee_id, name} | null, heartbeat_interval_seconds}`. The code is consumed. Any
   previous device token for that beacon stops working (re-pairing replaces the computer).
   Errors: 404 "Invalid or expired code" (same message for both).
3. Admin can unpair: `POST /beacons/{id}/unpair` → clears token, device and pairing.

`device_token`: 32 random bytes, url-safe base64, prefixed `adt_`. Sent as
`Authorization: Device <token>`.

## Desk heartbeat — `POST /desk/heartbeat`

Auth: `Authorization: Device <token>`. Body
`{locked: bool, idle_seconds: int, advertising: bool, agent_version: str}`.
Server updates `device` (`last_seen_at = now`) and returns
`{beacon: {uuid, major, minor}, heartbeat_interval_seconds, paired: true}` so the app picks up
changes (e.g. admin edited major/minor). 401 if the token is unknown (the app then shows
"This computer was unpaired" and returns to the pairing screen).

`heartbeat_interval_seconds` = `DESK_HEARTBEAT_INTERVAL_SECONDS` (60).

## Effect on presence

Setting `REQUIRE_DESK_ACTIVITY` (default true). When computing whether the phone is "near"
(`presence.py`), a matching reading for a `desk_app` beacon only counts if that desk app is
online (last heartbeat within `DESK_HEARTBEAT_GRACE_SECONDS`), not `locked`, and
`idle_seconds <= DESK_IDLE_LIMIT_SECONDS` (600). Tags are unaffected. So: phone near the desk but
computer locked/idle for 10+ minutes → `away`.

`presence_log` entries gain `reason` (`"near"`, `"no_signal"`, `"weak_signal"`, `"desk_locked"`,
`"desk_idle"`, `"desk_offline"`, `"outside"`, `null`) so the admin timeline can explain away time.

## Privacy

The desk app sends only: locked yes/no, seconds since last keyboard/mouse input, whether it is
broadcasting, app version, hostname and OS. No keystrokes, screenshots, window titles or app
usage. The consent text must mention this when the organisation uses desk apps.
