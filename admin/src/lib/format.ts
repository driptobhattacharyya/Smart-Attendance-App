import type { PresenceReason, PresenceStatus, SessionState } from "../api/attendance";

export function formatMinutes(total: number | null | undefined, empty = "—"): string {
  if (!total || total < 0) return empty;
  const h = Math.floor(total / 60);
  const m = Math.round(total % 60);
  if (!h) return `${m}m`;
  return `${h}h ${String(m).padStart(2, "0")}m`;
}

export function formatHours(total: number): string {
  return `${(total / 60).toFixed(1)}h`;
}

/** Local calendar date as YYYY-MM-DD (never shifts across UTC). */
export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Parse YYYY-MM-DD as a local date (new Date("2026-11-07") would be UTC midnight). */
export function parseDate(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}

export function startOfWeek(d: Date): Date {
  const r = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = (r.getDay() + 6) % 7; // Monday first
  return addDays(r, -diff);
}

export function dateRange(start: string, end: string): string[] {
  const out: string[] = [];
  const last = parseDate(end);
  for (let d = parseDate(start); d <= last; d = addDays(d, 1)) out.push(isoDate(d));
  return out;
}

export function fmtDay(s: string, opts: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat(undefined, opts).format(parseDate(s));
}

export function fmtRange(start: string, end: string): string {
  const o: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" };
  const oy: Intl.DateTimeFormatOptions = { ...o, year: "numeric" };
  if (start === end) return fmtDay(start, oy);
  return `${fmtDay(start, o)} – ${fmtDay(end, oy)}`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(
    new Date(iso),
  );
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function timeAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  const first = parts[0][0];
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase();
}

export const STATUS_LABEL: Record<PresenceStatus, string> = {
  present: "At desk",
  away: "Away from desk",
  stale: "Phone silent",
  outside: "Outside office",
  not_checked_in: "Not checked in",
  on_leave: "On leave",
};

export const STATUS_HELP: Record<PresenceStatus, string> = {
  present: "Checked in, inside the office and near their desk beacon.",
  away: "Checked in and in the office, but the phone cannot hear the desk beacon.",
  stale: "Checked in but the phone has not reported for a while.",
  outside: "Reported from outside the office geofence.",
  not_checked_in: "No active session today.",
  on_leave: "An approved leave request covers today.",
};

export const SESSION_STATE_LABEL: Record<SessionState | "silent", string> = {
  present: "At desk",
  away: "Away from desk",
  outside: "Outside office",
  silent: "No signal",
};

export function signalLevel(rssi: number | null | undefined): number {
  if (rssi == null) return 0;
  if (rssi >= -55) return 4;
  if (rssi >= -65) return 3;
  if (rssi >= -75) return 2;
  if (rssi >= -85) return 1;
  return 0;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (v: string) => UUID_RE.test(v.trim());

export function shortUuid(uuid: string): string {
  return `${uuid.slice(0, 8)}…${uuid.slice(-4)}`;
}

export function parseOptionalInt(value: string): number | null | undefined {
  const v = value.trim();
  if (v === "") return null;
  if (!/^\d+$/.test(v) || Number(v) > 65535) return undefined; // invalid
  return Number(v);
}

/** Plain-words explanation of why someone is away from their desk. */
export const REASON_LABEL: Partial<Record<PresenceReason, string>> = {
  desk_locked: "Computer locked",
  desk_idle: "Computer idle",
  desk_offline: "Computer offline",
  no_signal: "Phone can't hear desk",
  weak_signal: "Weak signal",
};
