/**
 * HTTP client for Attendify API v1.
 *
 * Base URL is `VITE_API_URL ?? "/api"` plus `/v1`. Errors follow architecture section 10:
 * `{"error": {"code", "message", "details"}}`, surfaced as `ApiError`.
 *
 * Token storage (security tradeoff, please read before changing):
 *  - The short-lived access token (15 min) lives in module memory only. It never touches
 *    storage, so a page reload starts with a silent refresh.
 *  - The refresh token (30 days, rotated on every use) is kept in localStorage so a reload or a
 *    new tab keeps the user signed in. The backend hands out refresh tokens as plain bearer
 *    tokens in the JSON body; it does not set an httpOnly cookie. The tradeoff: any script that
 *    runs on this origin (an XSS bug, a compromised dependency) can read localStorage and steal a
 *    30 day credential, whereas an httpOnly + SameSite cookie would be invisible to scripts.
 *    Mitigations here: rotation with reuse detection on the server (a stolen token that is used
 *    once revokes the whole session family), a strict dependency set, no HTML injection (React
 *    escapes everything), and the Cloudflare `_headers` CSP. If the backend ever moves refresh to
 *    a cookie, only `readRefresh` / `writeRefresh` / `refreshAccess` below need to change.
 *  - Reads and writes of storage are wrapped in try/catch: private windows and blocked site data
 *    make them throw, and the app then simply works without "stay signed in".
 */

export const API_BASE = `${(import.meta.env.VITE_API_URL ?? "/api").replace(/\/$/, "")}/v1`;

const REFRESH_KEY = "attendify_admin_refresh";

let accessToken: string | null = null;

function readRefresh(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

function writeRefresh(token: string | null) {
  try {
    if (token) localStorage.setItem(REFRESH_KEY, token);
    else localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* storage unavailable: the session just will not survive a reload */
  }
}

export function hasStoredSession(): boolean {
  return readRefresh() !== null;
}

/** Store the tokens returned by login, signup, invite-accept and refresh. */
export function setTokens(t: { access_token: string; refresh_token: string }) {
  accessToken = t.access_token;
  writeRefresh(t.refresh_token);
}

export function clearTokens() {
  accessToken = null;
  writeRefresh(null);
}

export function currentRefreshToken(): string | null {
  return readRefresh();
}

/* ------------------------------------------------------------------ errors */

export interface FieldIssue {
  loc: string[];
  msg: string;
  type: string;
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  retryAfter: number | null;
  constructor(
    message: string,
    status: number,
    code = "error",
    details: Record<string, unknown> = {},
    retryAfter: number | null = null,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryAfter = retryAfter;
  }
  /** The endpoint itself does not exist on this backend (not a missing record). */
  get unavailable() {
    return this.status === 405 || (this.status === 404 && this.code === "not_found" && this.message === "Not Found");
  }
  /** Per-field validation messages from a 422, keyed by the last path segment. */
  get fields(): Record<string, string> {
    const out: Record<string, string> = {};
    const issues = this.details.errors;
    if (Array.isArray(issues)) {
      for (const i of issues as FieldIssue[]) {
        const key = i.loc[i.loc.length - 1];
        if (key && !(key in out)) out[key] = i.msg;
      }
    }
    return out;
  }
}

async function toError(res: Response): Promise<ApiError> {
  const data = (await res.json().catch(() => null)) as {
    error?: { code?: string; message?: string; details?: Record<string, unknown> };
  } | null;
  const e = data?.error;
  const retry = Number(res.headers.get("Retry-After"));
  const retryAfter = Number.isFinite(retry) && retry > 0 ? retry : null;
  let message = e?.message ?? `Request failed (${res.status})`;
  if (e?.code === "validation_error") {
    const issues = (e.details?.errors ?? []) as FieldIssue[];
    if (issues.length) {
      message = issues
        .map((i) => {
          const field = i.loc[i.loc.length - 1];
          return field && field !== "body" ? `${field.replace(/_/g, " ")}: ${i.msg}` : i.msg;
        })
        .join("; ");
    }
  }
  if (res.status === 429) {
    message = retryAfter
      ? `Too many attempts. Try again in ${retryAfter < 90 ? `${retryAfter} seconds` : `${Math.ceil(retryAfter / 60)} minutes`}.`
      : "Too many attempts. Try again in a few minutes.";
  }
  return new ApiError(message, res.status, e?.code ?? `http_${res.status}`, e?.details ?? {}, retryAfter);
}

const NETWORK_ERROR = "Cannot reach the server. Check your connection and try again.";

/* ----------------------------------------------------------------- refresh */

let onUnauthorized: (() => void) | null = null;
/** Called once when the session cannot be recovered (refresh failed): the app signs out. */
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

let inflight: Promise<boolean> | null = null;

/** Run `fn` with a cross-tab lock when the browser supports it. */
function withLock<T>(fn: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  return locks ? (locks.request("attendify-admin-refresh", fn) as Promise<T>) : fn();
}

/**
 * Exchange the refresh token for a new pair. Single-flight within this tab (every concurrent 401
 * awaits the same promise) and serialised across tabs with the Web Locks API, because the server
 * treats a second use of a rotated token as theft and revokes the whole session.
 * Resolves false when the session is definitely gone, throws on network trouble.
 */
export function refreshAccess(): Promise<boolean> {
  if (!inflight) {
    inflight = withLock(async () => {
      // Another tab may have rotated the token while we waited for the lock, so always use
      // whatever is stored now. Rotation stays safe because the lock keeps refreshes sequential.
      const token = readRefresh();
      if (!token) return false;
      let res: Response;
      try {
        res = await fetch(`${API_BASE}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ refresh_token: token }),
        });
      } catch {
        throw new ApiError(NETWORK_ERROR, 0, "network_error");
      }
      if (res.ok) {
        const data = (await res.json()) as { access_token: string; refresh_token: string };
        setTokens(data);
        return true;
      }
      if (res.status === 401 || res.status === 400 || res.status === 403) {
        // Only discard the stored token if nobody else replaced it meanwhile.
        if (readRefresh() === token) clearTokens();
        return false;
      }
      throw await toError(res);
    }).finally(() => {
      inflight = null;
    });
  }
  return inflight;
}

/* ----------------------------------------------------------------- request */

export interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  /** false for endpoints that must work signed out (login, signup, reset...): no bearer, no refresh retry. */
  auth?: boolean;
  signal?: AbortSignal;
}

function url(path: string, query?: RequestOptions["query"]): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return `${API_BASE}${path}${s ? `?${s}` : ""}`;
}

function headers(json: boolean, auth: boolean): Headers {
  const h = new Headers({ Accept: "application/json" });
  if (auth && accessToken) h.set("Authorization", `Bearer ${accessToken}`);
  if (json) h.set("Content-Type", "application/json");
  return h;
}

async function send(path: string, opts: RequestOptions, retried = false): Promise<Response> {
  const { method = "GET", body, query, auth = true, signal } = opts;
  let res: Response;
  try {
    res = await fetch(url(path, query), {
      method,
      headers: headers(body !== undefined, auth),
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(NETWORK_ERROR, 0, "network_error");
  }
  if (res.status === 401 && auth && !retried) {
    // The access token expired (or was never loaded after a reload): refresh once and retry.
    const ok = await refreshAccess();
    if (ok) return send(path, opts, true);
    onUnauthorized?.();
  }
  return res;
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await send(path, opts);
  if (res.status === 204) return null as T;
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

/** Download an authenticated file (CSV) by fetching it as a blob. */
export async function download(path: string, fallbackName: string, query?: RequestOptions["query"]) {
  const res = await send(path, { query });
  if (!res.ok) throw await toError(res);
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") ?? "";
  const name = /filename="?([^";]+)"?/.exec(cd)?.[1] ?? fallbackName;
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}
