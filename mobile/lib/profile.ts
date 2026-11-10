// Who is signed in, what their organisation has switched on, and where their office is.
import { apiFetch } from "./api";
import { API_CONFIG } from "./config";
import { isApiError } from "./errors";

export type ModuleKey = "geofence" | "face" | "proximity" | "desk_app" | "wfh" | "regularization" | "leave";
export type Modules = Partial<Record<ModuleKey, boolean>>;

/** Used when the server omits a key. Optional extras are off, the core checks are on. */
const MODULE_DEFAULTS: Record<ModuleKey, boolean> = {
  geofence: true,
  face: true,
  proximity: false,
  desk_app: false,
  wfh: false,
  regularization: true,
  leave: true,
};

export const isModuleOn = (modules: Modules | null | undefined, key: ModuleKey): boolean =>
  modules?.[key] ?? MODULE_DEFAULTS[key];

export type Office = {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  radius_m: number;
  timezone?: string;
  address?: string | null;
  work_start?: string | null;
  work_end?: string | null;
  active?: boolean;
};

/** GET /people/me. `face_enrolled` and `office` are optional: see loadProfile(). */
export type Me = {
  id: string;
  name: string;
  email: string;
  employee_code?: string | null;
  role_key: string;
  office_id: string | null;
  permissions: string[];
  org: { id: string; name: string; slug: string; modules: Modules };
  consent_required: boolean;
  consent_version?: number;
  face_enrolled?: boolean;
  office?: Office | null;
};

export type Profile = {
  me: Me;
  modules: Modules;
  office: Office | null;
  faceEnrolled: boolean;
  consentRequired: boolean;
};

export const hasPermission = (me: Pick<Me, "permissions"> | null | undefined, permission: string) =>
  Boolean(me?.permissions?.includes(permission));

/** Build the Profile the app uses from a /people/me response and the office (if loaded). */
export function buildProfile(me: Me, office: Office | null, storedFaceEnrolled = false): Profile {
  return {
    me,
    modules: me.org?.modules ?? {},
    office: office ?? me.office ?? null,
    // Until the server reports it, fall back to what we saw last time.
    faceEnrolled: typeof me.face_enrolled === "boolean" ? me.face_enrolled : storedFaceEnrolled,
    consentRequired: Boolean(me.consent_required),
  };
}

export async function fetchOffice(id: string): Promise<Office | null> {
  try {
    return await apiFetch<Office>(API_CONFIG.ENDPOINTS.OFFICE(id));
  } catch (error) {
    // A missing office is a configuration problem, not a reason to block sign-in.
    if (isApiError(error) && (error.status === 404 || error.status === 403)) return null;
    throw error;
  }
}

/** Load /people/me and the user's office. Network errors propagate so the caller can go offline. */
export async function loadProfile(storedFaceEnrolled = false): Promise<Profile> {
  const me = await apiFetch<Me>(API_CONFIG.ENDPOINTS.ME);
  const office = me.office ?? (me.office_id ? await fetchOffice(me.office_id) : null);
  return buildProfile(me, office, storedFaceEnrolled);
}
