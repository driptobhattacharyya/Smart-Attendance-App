const BASE_URL = (import.meta.env.VITE_API_URL ?? "/api").replace(/\/$/, "");
const TOKEN_KEY = "attendify_admin_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session just won't persist */
  }
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
  /** The endpoint does not exist on this backend (older deployment). */
  get unavailable() {
    return this.status === 404 || this.status === 405;
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

function errorMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: { msg?: string }) => d.msg ?? "Invalid value").join("; ");
  }
  return fallback;
}

function headers(json: boolean): Headers {
  const h = new Headers({ Accept: "application/json" });
  const token = getToken();
  if (token) h.set("Authorization", `Bearer ${token}`);
  if (json) h.set("Content-Type", "application/json");
  return h;
}

async function fail(res: Response, isLogin: boolean): Promise<never> {
  const data = await res.json().catch(() => null);
  if (res.status === 401 && !isLogin) {
    setToken(null);
    onUnauthorized?.();
  }
  throw new ApiError(errorMessage(data, `Request failed (${res.status})`), res.status);
}

export async function api<T = unknown>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const { method = "GET", body } = opts;
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: headers(body !== undefined),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Cannot reach the server. Check your connection and try again.", 0);
  }
  if (res.status === 204) return null as T;
  if (!res.ok) return fail(res, path === "/auth/login");
  return (await res.json()) as T;
}

/** Download an authenticated file (CSV) by fetching it as a blob. */
export async function download(path: string, fallbackName: string) {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, { headers: headers(false) });
  } catch {
    throw new ApiError("Cannot reach the server. Check your connection and try again.", 0);
  }
  if (!res.ok) return fail(res, false);
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") ?? "";
  const name = /filename="?([^";]+)"?/.exec(cd)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
