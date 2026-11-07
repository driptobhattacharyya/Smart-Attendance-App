import * as SecureStore from "expo-secure-store";
import { API_CONFIG } from "./config";

export const TOKEN_KEY = "access_token";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
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
    throw new ApiError(errorMessage(data, `Request failed (${response.status})`), response.status);
  }
  return data as T;
}

/** Save everything the app needs after login/registration. */
export async function saveSession(auth: any) {
  const office = auth.office ?? {};
  await Promise.all([
    SecureStore.setItemAsync(TOKEN_KEY, auth.access_token),
    SecureStore.setItemAsync("employee_id", auth.employee_id),
    SecureStore.setItemAsync("office_id", office.id ?? ""),
    SecureStore.setItemAsync("office_name", office.name ?? "Office"),
    SecureStore.setItemAsync("office_latitude", String(office.latitude ?? "")),
    SecureStore.setItemAsync("office_longitude", String(office.longitude ?? "")),
    SecureStore.setItemAsync("office_radius", String(office.radius ?? "")),
    SecureStore.setItemAsync("bluetooth_device_id", auth.bluetooth_device_id ?? ""),
    SecureStore.setItemAsync("face_enrolled", auth.face_enrolled ? "true" : "false"),
  ]);
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
      "bluetooth_device_id",
      "face_enrolled",
      "person_id",
    ].map((k) => SecureStore.deleteItemAsync(k))
  );
}
