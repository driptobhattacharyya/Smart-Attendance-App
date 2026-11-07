# Attendify Desk

A small Windows tray app that turns an office computer into the Bluetooth "beacon" for Attendify.
Employees' phones measure its Bluetooth signal to confirm they are at their desk; the app also
reports whether the computer is unlocked and in use. Contract: `docs/api-contract-v4-desk-agent.md`.

## What it does

- Broadcasts a standard **iBeacon** (Apple company id `0x004C`, `02 15` + UUID + major + minor + power -59)
  using `BluetoothLEAdvertisementPublisher`. The phone app needs no changes.
- Sends a heartbeat (`POST /desk/heartbeat`, `Authorization: Device <token>`) every
  `heartbeat_interval_seconds` with `locked`, `idle_seconds`, `advertising`, `agent_version`.
- Keeps broadcasting during network outages (heartbeat retries with backoff up to 5 minutes).
- Tray icon: green = broadcasting and connected, amber = offline / Bluetooth off / paused, grey = not paired.

**What this app shares:** locked yes/no, seconds since last keyboard/mouse input, whether it is
broadcasting, app version, hostname and OS. No keystrokes, screenshots, window titles or app usage.
(Also available in the tray menu.)

## Install and pair

1. Download `AttendifyDesk.exe` from the GitHub Release (tag `desk-v*`) or the CI artifact.
2. Run it. A pairing window appears.
3. In the admin panel, open the desk beacon and create a pairing code (valid 15 minutes).
4. Enter the code (`ABCD-2345`; dash and case are handled for you) and press **Pair**.
   The server URL is under **Advanced** (default `https://smartattendance.driptobhattacharyya.com/api`).
5. Optional: tray menu -> **Start with Windows**.

The device token is encrypted with Windows DPAPI (current user) in
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
- **Phone does not see the desk**: confirm the tray is green, the beacon's major/minor in the admin
  panel match the log lines ("Publisher started for major=..."), and the phone is within a few metres.

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
5. Edit major/minor in admin: broadcast changes within one heartbeat.
6. Unpair in admin: pairing window shows "This computer was unpaired".
7. Second launch shows the "already running" message; Start with Windows toggle; Open logs.
8. PyInstaller exe works (winrt hidden imports), no console window.
