# Attendify Desk

A small Windows tray app that turns an office computer into the Bluetooth "beacon" for Attendify.
Employees' phones measure its Bluetooth signal to confirm they are at their desk; the app also
reports whether the computer is unlocked and in use. Contract: `docs/api-contract-v4-desk-agent.md`.

## What it does

- Broadcasts a standard **iBeacon** (Apple company id `0x004C`, `02 15` + UUID + major + minor + power -59)
  using `BluetoothLEAdvertisementPublisher`. The phone app needs no changes.
- The UUID is the organisation's desk UUID. **Major and minor rotate every 5 minutes** (see below).
- Sends a heartbeat (`POST /api/v1/desk/heartbeat`, `Authorization: Device <token>`) every
  `heartbeat_interval_seconds` with `locked`, `idle_seconds`, `advertising`, `agent_version`.
  The reply carries `server_time`, which the app uses to correct its clock.
- Keeps broadcasting during network outages (heartbeat retries with backoff up to 5 minutes).

## Rotating identity

A fixed major/minor could be copied into another device, which would then fake this desk's
presence. So the identity changes with time, and a copy stops working within minutes:

- At pairing the server issues a 32-byte `secret`, stored encrypted next to the device token.
- Every 5-minute window `w = floor(unix_seconds / 300)` the app computes
  `h = HMAC-SHA256(secret, w as 8-byte big-endian)` and broadcasts
  `major = h[0..1]` and `minor = h[2..3]` (both big-endian). The server computes the same values
  and accepts windows `w-1`, `w` and `w+1` to allow for clock drift.
- The app's clock is corrected with `server_time` from each heartbeat. The offset is smoothed
  (EWMA, weight 0.2); a single sample more than an hour away is ignored and logged. Three
  consecutive samples that agree with each other re-anchor the clock, so a machine whose clock
  really changed is not stuck.
- The identity is rebuilt when the window changes, at the window boundary (checked every second),
  which restarts the publisher. Heartbeat replies no longer carry major/minor.

Re-pairing or upgrading from 1.0 requires a new pairing code, because older configs have no secret.

### Rotating identity test vectors

Secret = bytes `00 01 02 ... 1f` (32 bytes, base64 `AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=`).
Any implementation can be checked against these (`tests/test_beacon.py`):

| window `w` | `h[0..3]` (hex) | major | minor |
|---|---|---|---|
| 0 | `9f0cd9b9` | 40716 | 55737 |
| 1 | `c432e059` | 50226 | 57433 |
| 5000000 | `26500d1a` | 9808 | 3354 |

- Tray icon: green = broadcasting and connected, amber = offline / Bluetooth off / paused, grey = not paired.

**What this app shares:** locked yes/no, seconds since last keyboard/mouse input, whether it is
broadcasting, app version, hostname and OS. No keystrokes, screenshots, window titles or app usage.
(Also available in the tray menu.)

## Install and pair

1. Download `AttendifyDesk.exe` from the GitHub Release (tag `desk-v*`) or the CI artifact.
2. Run it. A pairing window appears.
3. In the admin panel, open the desk beacon and create a pairing code (valid 15 minutes).
4. Enter the code (`ABCD-2345`; dash and case are handled for you) and press **Pair**.
   The server URL is under **Advanced** (default `https://smartattendance.driptobhattacharyya.com/api/v1`).
   Configs saved by 1.0 with a `/api` URL are migrated to `/api/v1` automatically.
5. Optional: tray menu -> **Start with Windows**.

The device token and the identity secret are both encrypted with Windows DPAPI (current user) in
`%APPDATA%\Attendify Desk\config.json`. Logs: `%LOCALAPPDATA%\Attendify Desk\logs` (tray -> Open logs);
the token is never logged.

"Pause for 15 minutes" stops broadcasting and reports `advertising=false`, so the employee will
show as **away** until it resumes.

## Troubleshooting

- **"Bluetooth is off"**: turn Bluetooth on in Windows Settings. The app rechecks every 30 seconds.
- **"No Bluetooth adapter found"**: the PC has no usable Bluetooth radio (or the driver is missing).
- **"adapter does not support broadcasting"**: some older/cheap adapters cannot advertise
  (`BluetoothAdapter.IsPeripheralRoleSupported` / `IsAdvertisementOffloadSupported` false and the publisher aborts).
  Use a different USB Bluetooth 4.0+/5 dongle, or use a physical beacon tag instead.
- **"This computer was unpaired"**: an admin unpaired it or re-paired another computer. Enter a new code.
- **Phone does not see the desk**: confirm the tray is green and the phone is within a few metres.
  Major/minor change every 5 minutes, so compare the log lines ("Publisher started for major=...")
  only with a value from the same window. If the clock is badly wrong, the log shows
  "Ignoring clock sample" or "re-anchoring".

## IT deployment

- Single file, no installer, no admin rights: copy `AttendifyDesk.exe` to e.g.
  `%LOCALAPPDATA%\Programs\AttendifyDesk\` per user.
- Silent "install": script the copy, then set the per-user startup entry
  `reg add HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v "Attendify Desk" /t REG_SZ /d "\"<path>\AttendifyDesk.exe\"" /f`.
  (Or users toggle **Start with Windows** in the tray.) Startup is per user, not per machine.
- Pairing is per Windows user; the token is DPAPI-bound to that user.
- Outbound HTTPS to the Attendify server is the only network need. The exe is unsigned unless your CI adds code signing.

## Development

```
cd desk
uv sync --all-groups
uv run pytest -q
uv run ruff check .
uv run --group build pyinstaller attendify_desk.spec --noconfirm   # on Windows
```

Windows-only code (WinRT advertising, DPAPI, activity probe, registry, mutex) is imported lazily
and is not exercised by the Linux tests; fakes stand in for it.

## To verify on a real Windows PC

1. App starts, pairing works, config.json holds an encrypted token.
2. A BLE scanner (nRF Connect) or the Attendify phone app sees the iBeacon with the right UUID/major/minor.
3. Toggle Bluetooth off/on: tray shows "Bluetooth is off", recovers within ~30 s.
4. Lock the PC / stop input: heartbeat `locked` and `idle_seconds` reflect it.
5. Major/minor change at each 5-minute boundary (check against the test vectors with a known secret),
   and the server accepts the phone's readings through the boundary.
6. Unpair in admin: pairing window shows "This computer was unpaired".
7. Second launch shows the "already running" message; Start with Windows toggle; Open logs.
8. PyInstaller exe works (winrt hidden imports), no console window.
