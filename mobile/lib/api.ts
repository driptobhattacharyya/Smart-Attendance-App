import * as SecureStore from "expo-secure-store";
import { API_CONFIG } from "./config";

export const TOKEN_KEY = "access_token";

/** Values the background task reads (it runs without React state). */
export const BEACONS_KEY = "my_beacons";
export const HEARTBEAT_INTERVAL_KEY = "heartbeat_interval_seconds";
const TASK_KEYS = [BEACONS_KEY, HEARTBEAT_INTERVAL_KEY];

/**
 * The token must be readable while the phone is locked, otherwise background
 * heartbeats would fail with 401. AFTER_FIRST_UNLOCK allows that.
 */
export const SECURE_BACKGROUND_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK,
};

export const CONSENT_REQUIRED_KEY = "consent_required";
/** 403 detail the API returns while the user has not accepted the current privacy notice. */
const CONSENT_REQUIRED_DETAIL = "Consent required";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function isConsentRequiredError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403 && error.message === CONSENT_REQUIRED_DETAIL;
}

let consentRequiredListener: (() => void) | null = null;

/** AppContext registers this so any 403 "Consent required" reopens the consent screen. */
export function setConsentRequiredListener(listener: (() => void) | null) {
  consentRequiredListener = listener;
}

function errorMessage(body: any, fallback: string): string {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join("; ");
  return fallback;
}

/**
 * Call the Attendify API. Adds the saved login token, sends JSON (or FormData
 * for photo uploads) and throws ApiError with the server's message on failure.
 */
export async function apiFetch<T = any>(
  path: string,
  options: { method?: string; json?: unknown; form?: FormData; token?: string } = {}
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = options.token ?? (await SecureStore.getItemAsync(TOKEN_KEY));
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (options.form) {
    // Let fetch set the multipart boundary itself.
    body = options.form;
  } else if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }

  const response = await fetch(`${API_CONFIG.BASE_URL}${path}`, {
    method: options.method ?? (body ? "POST" : "GET"),
    headers,
    body,
  });
  if (response.status === 204) return null as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new ApiError(errorMessage(data, `Request failed (${response.status})`), response.status);
    if (isConsentRequiredError(error)) consentRequiredListener?.();
    throw error;
  }
  return data as T;
}

/** Save everything the app needs after login/registration. */
export async function saveSession(auth: any) {
  const office = auth.office ?? {};
  await Promise.all([
    SecureStore.setItemAsync(TOKEN_KEY, auth.access_token, SECURE_BACKGROUND_OPTIONS),
    SecureStore.setItemAsync("employee_id", auth.employee_id),
    SecureStore.setItemAsync("office_id", office.id ?? ""),
    SecureStore.setItemAsync("office_name", office.name ?? "Office"),
    SecureStore.setItemAsync("office_latitude", String(office.latitude ?? "")),
    SecureStore.setItemAsync("office_longitude", String(office.longitude ?? "")),
    SecureStore.setItemAsync("office_radius", String(office.radius ?? "")),
    SecureStore.setItemAsync("face_enrolled", auth.face_enrolled ? "true" : "false"),
    SecureStore.setItemAsync(CONSENT_REQUIRED_KEY, auth.consent_required ? "true" : "false"),
  ]);
}

/** Last known consent state, for starting up while offline. */
export async function loadStoredConsentRequired(): Promise<boolean> {
  try {
    return (await SecureStore.getItemAsync(CONSENT_REQUIRED_KEY)) === "true";
  } catch {
    return false;
  }
}

export async function storeConsentRequired(required: boolean) {
  try {
    await SecureStore.setItemAsync(CONSENT_REQUIRED_KEY, required ? "true" : "false");
  } catch {
    /* ignore: the in-memory state still applies */
  }
}

export async function clearSession() {
  await Promise.all(
    [
      TOKEN_KEY,
      "employee_id",
      "office_id",
      "office_name",
      "office_latitude",
      "office_longitude",
      "office_radius",
      "bluetooth_device_id", // legacy key from older app versions
      ...TASK_KEYS,
      "face_enrolled",
      "person_id",
      CONSENT_REQUIRED_KEY,
    ].map((k) => SecureStore.deleteItemAsync(k))
  );
}
