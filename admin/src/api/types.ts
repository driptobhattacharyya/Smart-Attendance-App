import type { components } from "./schema";

/** Shapes generated from the backend OpenAPI (`npm run gen:api`), narrowed where the schema is loose. */
type S = components["schemas"];

export type User = S["UserOut"];
export type Me = S["MeOut"];
export type Department = S["DepartmentOut"];
export type Office = S["OfficeOut"];
export type OfficeInput = S["OfficeCreate"];
export type DeskDevice = S["DeskDeviceOut"];
export type PairingCode = S["PairingCodeOut"];
export type Session = S["SessionOut"];
export type AuditEntry = S["AuditOut"];
export type ConsentText = S["ConsentTextOut"];
export type LoginOut = S["LoginOut"];
export type SignupOut = S["SignupOut"];
export type TokenPair = S["TokenPair"];

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

/* ------------------------------------------------------------ modules / settings */

export const MODULE_KEYS = ["geofence", "face", "proximity", "desk_app", "wfh", "regularization", "leave"] as const;
export type ModuleKey = (typeof MODULE_KEYS)[number];
export type Modules = Record<ModuleKey, boolean>;

export interface OrgSettings {
  block_rooted_devices?: boolean;
  presence_retention_days?: number;
  desk_activity_monitoring?: boolean;
  desk_uuid?: string;
  plan?: { name?: string; max_employees?: number };
}

export interface Org extends Omit<S["OrgOut"], "modules" | "settings"> {
  modules: Partial<Modules>;
  settings: OrgSettings;
}

export interface OrgPatch {
  name?: string;
  modules?: Partial<Modules>;
  settings?: Pick<OrgSettings, "block_rooted_devices" | "presence_retention_days" | "desk_activity_monitoring">;
  bump_consent_version?: boolean;
}

/* ------------------------------------------------------------------ permissions */

/** Permission keys from docs/architecture-v5.md section 4. The UI checks these, never role names. */
export const P = {
  attendanceSelf: "attendance.self",
  viewTeam: "attendance.view.team",
  viewOrg: "attendance.view.org",
  approveTeam: "requests.approve.team",
  approveOrg: "requests.approve.org",
  correct: "attendance.correct",
  peopleTeam: "people.view.team",
  peopleManage: "people.manage",
  placesManage: "places.manage",
  policiesManage: "policies.manage",
  export: "reports.export",
  audit: "audit.view",
  orgManage: "org.manage",
} as const;

/** Built-in role keys offered when inviting or editing people (labels only; the server decides what they can do). */
export const ROLE_OPTIONS = [
  { key: "employee", label: "Employee", hint: "Checks in and out, sees their own records." },
  { key: "manager", label: "Manager", hint: "Sees and approves requests for their reporting line." },
  { key: "admin", label: "Administrator", hint: "Manages people, offices, devices and the audit log." },
  { key: "owner", label: "Owner", hint: "Everything, including company settings." },
] as const;

export function roleLabel(key: string): string {
  return ROLE_OPTIONS.find((r) => r.key === key)?.label ?? key;
}
