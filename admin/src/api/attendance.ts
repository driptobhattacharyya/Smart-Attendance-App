/**
 * Everything the console knows about the attendance part of the API lives in this one module:
 * endpoint paths, response shapes and the react-query hooks built on them. The attendance engine
 * is being built in parallel (docs/api-v1.md does not list it yet), so the paths below are the
 * ones the architecture document (sections 7 and 11) implies.
 *
 * Until the backend serves them, every call 404s and the pages show their "not available yet"
 * state (see ErrorState). Nothing here is generated from OpenAPI yet.
 *
 * TODO for the integrator, in order:
 *  1. Run `npm run gen:api`, then check each path below against `src/api/schema.d.ts` and fix `PATHS`.
 *  2. Replace the hand-written interfaces with `components["schemas"][...]` once the schemas exist.
 *  3. Dashboard (`/attendance/dashboard`): confirm the field names. The old per-beacon / per-face
 *     counters (`employees_without_face`, `employees_without_beacon`, ...) are optional here and
 *     the Overview only renders the ones that are present.
 *  4. Presence board: rows are keyed by `user_id` (uuid) and show `employee_code`.
 *  5. Timesheets: confirm the query names (`start`, `end`, `office_id`) and that the CSV export
 *     is `GET` with a `Content-Disposition` filename.
 *  6. Requests: the v1 model has `kind` correction | leave | wfh and statuses in lower case in
 *     the DB; `normalizeStatus` below accepts either spelling. Confirm the decision body
 *     (`{action: "approve" | "reject", comment?: string}`).
 *  7. Session detail (timeline drill-down) path and `presence_log` shape.
 *  8. Scoping: managers get the same endpoints scoped to their reporting tree by permission
 *     (`attendance.view.team` vs `.org`); the UI no longer chooses `/admin` or `/manager` itself.
 */
import { useQuery } from "@tanstack/react-query";
import { api, download } from "./client";

export const PATHS = {
  dashboard: "/attendance/dashboard",
  presence: "/attendance/presence",
  timesheets: "/attendance/timesheets",
  timesheetsExport: "/attendance/timesheets/export.csv",
  requests: "/requests",
  requestDecision: (id: string) => `/requests/${id}/decision`,
  session: (id: string) => `/attendance/sessions/${id}`,
} as const;

/* ------------------------------------------------------------------- shapes */

/** Why a person is away, as the presence log records it (desk-aware). */
export type PresenceReason =
  | "near"
  | "no_signal"
  | "weak_signal"
  | "desk_locked"
  | "desk_idle"
  | "desk_offline"
  | "outside";

export type PresenceStatus = "present" | "away" | "stale" | "outside" | "not_checked_in" | "on_leave";

export interface PresenceRow {
  user_id: string;
  employee_code: string;
  name: string;
  office_id: string | null;
  status: PresenceStatus;
  checked_in: boolean;
  session_id: string | null;
  since: string | null;
  last_heartbeat_at: string | null;
  best_rssi: number | null;
  reason?: PresenceReason | null;
  today_minutes: number;
  has_beacon?: boolean;
}

export interface Dashboard {
  date: string;
  headcount: Record<PresenceStatus | "total", number>;
  pending_requests: number;
  unassigned_beacons?: number | null;
  unpaired_desk_apps?: number | null;
  employees_without_face?: number;
  employees_without_beacon?: number;
  daily: { date: string; worked_minutes: number; away_minutes: number; people: number }[];
  late_today: { user_id: string; name: string; first_check_in: string }[];
}

export type SessionState = "present" | "away" | "outside";

export interface AttendanceSession {
  id: string;
  date: string;
  started_at: string;
  ended_at: string | null;
  source: "app" | "desk" | "admin" | "request";
  minutes: number;
  present_minutes: number;
  away_minutes: number;
  state: SessionState | null;
}

export interface PresenceLogEntry {
  at: string;
  state: SessionState;
  distance_m: number | null;
  best_rssi: number | null;
}

export interface SessionDetail extends AttendanceSession {
  user_id: string;
  employee_code: string;
  name: string;
  office_id: string | null;
  check_in?: {
    latitude: number;
    longitude: number;
    distance_m: number | null;
    best_rssi: number | null;
  } | null;
  presence_log: PresenceLogEntry[];
}

export interface DaySummary {
  date: string;
  total_minutes: number;
  sessions: AttendanceSession[];
  on_leave: boolean;
}

export interface AttendanceReport {
  user_id: string;
  employee_code: string;
  name: string;
  start: string;
  end: string;
  total_minutes: number;
  days: DaySummary[];
}

export type RequestStatus = "Pending" | "Approved" | "Rejected";

export interface AttendanceRequest {
  id: string;
  user_id: string;
  employee_code?: string;
  employee_name: string | null;
  reason: string;
  start_date: string;
  end_date: string;
  entry_time: string | null;
  exit_time: string | null;
  kind: "correction" | "leave" | "wfh";
  status: RequestStatus;
  submitted_at: string;
  decided_by: string | null;
  decided_at: string | null;
  comment: string | null;
}

/** Accepts "pending" or "Pending" from the server and returns the display spelling. */
export function normalizeStatus(s: string): RequestStatus {
  const v = s.toLowerCase();
  return v === "approved" ? "Approved" : v === "rejected" ? "Rejected" : "Pending";
}

/* -------------------------------------------------------------------- hooks */

export function useDashboard(enabled = true) {
  return useQuery({
    queryKey: ["dashboard"],
    enabled,
    queryFn: () => api<Dashboard>(PATHS.dashboard),
    refetchInterval: 60_000,
    retry: false,
  });
}

export function usePresence(officeId: string) {
  return useQuery({
    queryKey: ["presence", officeId],
    queryFn: () => api<PresenceRow[]>(PATHS.presence, { query: { office_id: officeId } }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false, // pauses while the tab is hidden
    staleTime: 10_000,
  });
}

export function useTimesheets(start: string, end: string, enabled = true) {
  return useQuery({
    queryKey: ["timesheets", start, end],
    enabled,
    queryFn: () => api<AttendanceReport[]>(PATHS.timesheets, { query: { start, end } }),
  });
}

export function exportTimesheets(start: string, end: string) {
  return download(PATHS.timesheetsExport, `attendance_${start}_${end}.csv`, { start, end });
}

/** Only call when the user may approve requests; a 404 simply hides the nav badge. */
export function useRequests(enabled = true) {
  return useQuery({
    queryKey: ["requests"],
    enabled,
    retry: false,
    queryFn: async () => {
      const rows = await api<AttendanceRequest[]>(PATHS.requests);
      return rows.map((r) => ({ ...r, status: normalizeStatus(r.status) }));
    },
  });
}

export function decideRequest(id: string, action: "approve" | "reject", comment: string) {
  return api(PATHS.requestDecision(id), { method: "POST", body: { action, comment: comment || null } });
}

export function useSessionDetail(id: string) {
  return useQuery({
    queryKey: ["session", id],
    queryFn: () => api<SessionDetail>(PATHS.session(id)),
  });
}
