# Attendify

Attendify is a smart attendance system. An employee's time only counts while three things hold at once: the phone is inside the office geofence, the employee has passed a face check to start a session, and the phone hears their desk Bluetooth beacon. The server stamps every session with its own timestamps and decides what counts, so being in the building but away from your desk is recorded as away time, not worked time. Desk beacons (iBeacon) are part of the v2 design in [`docs/api-contract-v2.md`](docs/api-contract-v2.md).

## Repository layout

| Path | Contents |
|---|---|
| `backend/` | FastAPI API on MongoDB: auth, offices, face verification (OpenCV), check-in/out, requests, manager and admin endpoints. |
| `admin/` | Admin panel for managers and admins (React and Vite). |
| `mobile/` | Employee app (React Native with Expo). |
| `docs/` | Design documents, including the API contract. |
| `infra/` | Deployment templates. |

## Quick start (local development)

You need Python 3.11+ with [uv](https://docs.astral.sh/uv/), Node.js, and Docker (for MongoDB).

### 1. Database

```bash
docker run -p 27017:27017 mongo:7
```

### 2. Backend

```bash
cd backend
cp .env.example .env
uv sync
sh scripts/download_models.sh
uv run uvicorn app.main:app --reload
```

The API is served at `http://localhost:8000` under `/api`, with interactive docs at `/docs`. Without the face models the API still starts, but face verification is disabled. To reach the API from a phone on your network, add `--host 0.0.0.0`.

Seed an office and the first accounts. The coordinates are the office location, and the script prints a generated password for each account it creates:

```bash
uv run python -m scripts.seed --lat <latitude> --lon <longitude> --tz Asia/Kolkata
```

### 3. Admin panel

```bash
cd admin
npm install
npm run dev
```

Open `http://localhost:5173` and sign in with a manager or admin account from the seed. The API address defaults to `http://localhost:8000/api`; override it with `VITE_API_URL` in `admin/.env` (see `admin/.env.example`).

### 4. Mobile app

```bash
cd mobile
cp .env.example .env    # set EXPO_PUBLIC_API_URL
npm install
npx expo run:android
```

- Build a **development build**, not Expo Go. Bluetooth and the camera do not work in Expo Go. `npx expo run:android` builds and installs one.
- `EXPO_PUBLIC_API_URL` must point at your computer's LAN IP, for example `http://192.168.1.10:8000/api`. `localhost` means the phone itself. On the Android emulator, use `http://10.0.2.2:8000/api`.

## Tests

```bash
cd backend && uv run pytest     # uses an in-memory MongoDB mock, no database needed
cd backend && uv run ruff check .
cd mobile && npx jest           # single run; `npm test` starts watch mode
cd admin && npm run build       # the admin panel has no unit tests yet
```

## Documentation

- [`docs/api-contract-v2.md`](docs/api-contract-v2.md): the API contract shared by the backend, mobile app and admin panel.
- [`docs/deploy.md`](docs/deploy.md): deployment guide (in progress).
- Interactive API reference: `/docs` on a running backend.

## History

This repository consolidates earlier projects into one monorepo:

- **AttendifyBackend** and **wolverine-deadpool**: earlier Flask backends, replaced by `backend/`.
- **adminsmartattendance**: an earlier admin panel mock-up, replaced by `admin/`.
- **SmartAttendance** and **Smart-Attendance**: unrelated Flutter experiments.

The older repositories are to be archived.
