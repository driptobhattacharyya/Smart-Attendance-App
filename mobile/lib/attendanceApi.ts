// Attendance endpoints (architecture sections 7 and 8). These are being built in parallel with
// this app; every path and payload builder is here so they are easy to adjust.
//
// Assumed shapes (same as contract v2 unless architecture-v5 says otherwise):
//   GET  /attendance/status      -> AttendanceStatus
//   POST /attendance/check-in    -> {session}            body: CheckInBody
//   POST /attendance/heartbeat   -> HeartbeatResponse    body: HeartbeatBody
//   POST /attendance/check-out   -> {session}            body: CheckOutBody
//   GET  /attendance/integrity-nonce -> {nonce}          (nonce for Play Integrity; optional)
//   GET  /attendance/me?start=&end= -> AttendanceReport
//   POST /requests {kind, ...}   -> request;  GET /requests/me -> request[]
// Check-in, check-out and heartbeat send an Idempotency-Key (phones retry).
import { apiFetch } from "./api";
import { BeaconReading } from "./beacons";
import { TrustPayload } from "./trust";

export const ATTENDANCE_ENDPOINTS = {
  STATUS: "/attendance/status",
  CHECK_IN: "/attendance/check-in",
  HEARTBEAT: "/attendance/heartbeat",
  CHECK_OUT: "/attendance/check-out",
  INTEGRITY_NONCE: "/attendance/integrity-nonce",
  MY_ATTENDANCE: "/attendance/me",
  SUBMIT_REQUEST: "/requests",
  MY_REQUESTS: "/requests/me",
} as const;

export type PresenceState = "present" | "away" | "outside";

export type SessionOut = {
  id: string;
  date: string;
  started_at: string;
  ended_at: string | null;
  source: "app" | "request" | string;
  /** "remote" for a work-from-home session. */
  mode?: "onsite" | "remote";
  minutes: number;
  present_minutes?: number;
  away_minutes?: number;
  state?: PresenceState | null;
};

export type AttendanceStatus = {
  session: SessionOut | null;
  today_minutes: number;
  presence?: PresenceState | null;
  face_verified_until?: string | null;
  heartbeat_interval_seconds?: number;
};

export type HeartbeatResponse = {
  state: PresenceState;
  checked_in: boolean;
  session: SessionOut | null;
  next_heartbeat_seconds: number;
};

export type CheckInMode = "onsite" | "remote";

export type CheckInBody = TrustPayload & {
  /** "remote" is work from home (module `wfh`): the server skips geofence and proximity, keeps face. */
  mode: CheckInMode;
  beacons: BeaconReading[];
};
export type HeartbeatBody = TrustPayload & { beacons: BeaconReading[] };
export type CheckOutBody = Partial<TrustPayload>;

export function buildCheckInBody(input: {
  trust: TrustPayload;
  beacons: BeaconReading[];
  mode?: CheckInMode;
}): CheckInBody {
  return { mode: input.mode ?? "onsite", ...input.trust, beacons: input.mode === "remote" ? [] : input.beacons };
}

export const buildHeartbeatBody = (input: { trust: TrustPayload; beacons: BeaconReading[] }): HeartbeatBody => ({
  ...input.trust,
  beacons: input.beacons,
});

export const buildCheckOutBody = (trust?: TrustPayload | null): CheckOutBody => (trust ? { ...trust } : {});

export const fetchStatus = () => apiFetch<AttendanceStatus>(ATTENDANCE_ENDPOINTS.STATUS);

export const checkIn = (body: CheckInBody, idempotencyKey: string) =>
  apiFetch<{ session?: SessionOut }>(ATTENDANCE_ENDPOINTS.CHECK_IN, { json: body, idempotencyKey });

export const sendHeartbeat = (body: HeartbeatBody, idempotencyKey: string) =>
  apiFetch<HeartbeatResponse>(ATTENDANCE_ENDPOINTS.HEARTBEAT, { json: body, idempotencyKey });

export const checkOut = (body: CheckOutBody, idempotencyKey: string) =>
  apiFetch<{ session?: SessionOut }>(ATTENDANCE_ENDPOINTS.CHECK_OUT, { json: body, idempotencyKey });

/** Server nonce used as the Play Integrity request hash. Null when the server has none. */
export async function fetchIntegrityNonce(): Promise<string | null> {
  try {
    const data = await apiFetch<{ nonce?: string }>(ATTENDANCE_ENDPOINTS.INTEGRITY_NONCE);
    return data?.nonce ?? null;
  } catch {
    return null;
  }
}

export type AttendanceReport = {
  name?: string;
  days: {
    date: string;
    total_minutes: number;
    sessions: { id?: string; started_at: string; ended_at: string | null; minutes?: number }[];
  }[];
};

export const fetchMyAttendance = (range?: { start: string; end: string }) =>
  apiFetch<AttendanceReport>(
    range
      ? `${ATTENDANCE_ENDPOINTS.MY_ATTENDANCE}?start=${range.start}&end=${range.end}`
      : ATTENDANCE_ENDPOINTS.MY_ATTENDANCE
  );

/** Request kinds in architecture section 7. `leave` and `wfh` need their modules. */
export type RequestKind = "correction" | "leave" | "wfh";

export type RequestBody = {
  kind: RequestKind;
  reason: string;
  start_date: string;
  end_date: string;
  entry_time?: string;
  exit_time?: string;
};

export const submitRequest = (body: RequestBody) =>
  apiFetch(ATTENDANCE_ENDPOINTS.SUBMIT_REQUEST, { json: body });
