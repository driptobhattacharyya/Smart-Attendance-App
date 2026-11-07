export type Role = "employee" | "manager" | "admin";

export interface Office {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  radius: number;
  timezone: string;
  address?: string | null;
  work_start?: string | null;
}

export interface User {
  employee_id: string;
  name: string;
  email: string;
  phone?: string | null;
  role: Role;
  team_id?: string | null;
  office_id?: string | null;
  bluetooth_device_id?: string | null;
  face_enrolled: boolean;
  active: boolean;
  consent_required?: boolean;
}

export interface AuthOut {
  access_token: string;
  user: User;
  office?: Office | null;
}

export interface Beacon {
  id: string;
  office_id: string;
  name: string;
  uuid: string;
  major: number | null;
  minor: number | null;
  assigned_employee_id: string | null;
  active: boolean;
}

export type PresenceStatus =
  | "present"
  | "away"
  | "stale"
  | "outside"
  | "not_checked_in"
  | "on_leave";

export interface PresenceRow {
  employee_id: string;
  name: string;
  office_id: string | null;
  status: PresenceStatus;
  checked_in: boolean;
  session_id: string | null;
  since: string | null;
  last_heartbeat_at: string | null;
  best_rssi: number | null;
  today_minutes: number;
  face_enrolled: boolean;
  has_beacon: boolean;
}

export interface Dashboard {
  date: string;
  headcount: Record<PresenceStatus | "total", number>;
  pending_requests: number;
  unassigned_beacons: number | null;
  employees_without_face: number;
  employees_without_beacon: number;
  daily: { date: string; worked_minutes: number; away_minutes: number; people: number }[];
  late_today: { employee_id: string; name: string; first_check_in: string }[];
}

export type SessionState = "present" | "away" | "outside";

export interface Session {
  id: string;
  date: string;
  started_at: string;
  ended_at: string | null;
  source: "app" | "request";
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

export interface SessionDetail extends Session {
  employee_id: string;
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
  sessions: Session[];
  on_leave: boolean;
}

export interface AttendanceReport {
  employee_id: string;
  name: string;
  start: string;
  end: string;
  total_minutes: number;
  days: DaySummary[];
}

export interface AttendanceRequest {
  id: string;
  employee_id: string;
  employee_name: string | null;
  reason: string;
  start_date: string;
  end_date: string;
  entry_time: string | null;
  exit_time: string | null;
  kind: "correction" | "leave";
  status: "Pending" | "Approved" | "Rejected";
  submitted_at: string;
  decided_by: string | null;
  decided_at: string | null;
  comment: string | null;
}
