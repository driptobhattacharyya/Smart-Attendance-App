// HTTP client for the Attendify API v1.
//
// - The access token (JWT, 15 min) lives in memory only. The refresh token (opaque, 30 days,
//   rotated on every use) lives in SecureStore so the background task can use it too.
// - A 401 triggers ONE refresh shared by every request in flight (single flight), then the
//   request is retried. A refresh that fails with 401 ends the session: tokens are wiped and
//   the auth-lost listener sends the user back to the login screen with a message.
// - Requests with an `idempotencyKey` send an `Idempotency-Key` header and are retried on
//   network errors and 5xx with the SAME key, so the server never records them twice.
import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";
import { API_CONFIG, BASE_URL, PUBLIC_PATHS } from "./config";
import { ApiError, isConsentRequiredError, isNetworkError, networkError, parseApiError } from "./errors";

export { ApiError, isConsentRequiredError, isNetworkError } from "./errors";

export const REFRESH_TOKEN_KEY = "refresh_token";

/** Values the background task reads (it runs without React state). */
export const DESK_DEVICES_KEY = "my_desk_devices";
export const HEARTBEAT_INTERVAL_KEY = "heartbeat_interval_seconds";
export const PROFILE_CACHE_KEY = "profile_cache";
const CACHE_KEYS = [DESK_DEVICES_KEY, HEARTBEAT_INTERVAL_KEY, PROFILE_CACHE_KEY];

/**
 * The refresh token must be readable while the phone is locked, otherwise background
 * heartbeats would fail. AFTER_FIRST_UNLOCK allows that.
 */
export const SECURE_BACKGROUND_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK,
};

// ---- tokens --------------------------------------------------------------------------

let accessToken: string | null = null;
let refreshInFlight: Promise<boolean> | null = null;

export const getAccessToken = () => accessToken;
export const setAccessToken = (token: string | null) => {
  accessToken = token;
};

export async function getRefreshToken(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
  } catch {
    return null;
  }
}

export async function saveTokens(tokens: { access_token: string; refresh_token: string }) {
  accessToken = tokens.access_token;
  await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, tokens.refresh_token, SECURE_BACKGROUND_OPTIONS);
}

export async function clearTokens() {
  accessToken = null;
  try {
    await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
  } catch {
    /* nothing stored */
  }
}

/** Forget the tokens and everything cached for the signed-in user. */
export async function clearSession() {
  await clearTokens();
  await Promise.all(CACHE_KEYS.map((k) => SecureStore.deleteItemAsync(k).catch(() => {})));
}

// ---- listeners -----------------------------------------------------------------------

let consentRequiredListener: (() => void) | null = null;
let authLostListener: ((message: string) => void) | null = null;

/** AppContext registers this so any 403 consent_required reopens the consent screen. */
export function setConsentRequiredListener(listener: (() => void) | null) {
  consentRequiredListener = listener;
}

/** AppContext registers this to return to the login screen when the session cannot be refreshed. */
export function setAuthLostListener(listener: ((message: string) => void) | null) {
  authLostListener = listener;
}

const AUTH_LOST_MESSAGES: Record<string, string> = {
  token_reuse_detected: "Your session was ended for security reasons. Please sign in again.",
  session_revoked: "You were signed out (from another device or by your admin). Please sign in again.",
  account_disabled: "Your account has been disabled. Contact your admin.",
};
const DEFAULT_AUTH_LOST = "Your session has expired. Please sign in again.";

async function endSession(code?: string) {
  await clearTokens();
  authLostListener?.((code && AUTH_LOST_MESSAGES[code]) || DEFAULT_AUTH_LOST);
}

// ---- helpers -------------------------------------------------------------------------

export function newIdempotencyKey(): string {
  try {
    return Crypto.randomUUID();
  } catch {
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }
}

let retryDelayMs = 600;
/** Tests set this to 0. */
export const setRetryDelay = (ms: number) => {
  retryDelayMs = ms;
};
const sleep = (ms: number) => (ms > 0 ? new Promise((r) => setTimeout(r, ms)) : Promise.resolve());

async function readBody(response: Response): Promise<any> {
  if (response.status === 204) return null;
  return response.json().catch(() => null);
}

async function toError(response: Response): Promise<ApiError> {
  const err = parseApiError(await readBody(response), response.status);
  const retryAfter = Number(response.headers?.get?.("Retry-After"));
  if (retryAfter > 0) err.retryAfter = retryAfter;
  return err;
}

async function doFetch(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw networkError();
  }
}

// ---- refresh (single flight) --------------------------------------------------------

async function runRefresh(): Promise<boolean> {
  const refreshToken = await getRefreshToken();
  if (!refreshToken) {
    await endSession();
    return false;
  }
  const response = await doFetch(`${BASE_URL}${API_CONFIG.ENDPOINTS.REFRESH}`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (response.ok) {
    const data = await readBody(response);
    await saveTokens(data);
    return true;
  }
  const error = await toError(response);
  if (response.status === 400 || response.status === 401 || response.status === 403) {
    await endSession(error.code);
    return false;
  }
  throw error; // 429 / 5xx: try again later, keep the session
}

/**
 * Exchange the refresh token for a new access token. Concurrent callers share one request:
 * the refresh token is single use, so two parallel refreshes would trip theft detection.
 * Resolves false when the session is over (the auth-lost listener has been called).
 */
export function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = runRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

// ---- apiFetch ------------------------------------------------------------------------

export type ApiOptions = {
  method?: string;
  json?: unknown;
  form?: FormData;
  /** Force sending/not sending the access token. Default: everything except the public auth paths. */
  auth?: boolean;
  /** Sent as the Idempotency-Key header; also enables retries on network errors and 5xx. */
  idempotencyKey?: string;
  /** Extra attempts after the first (default 2 when an idempotencyKey is set, else 0). */
  retries?: number;
};

const sessionExpired = () => new ApiError(DEFAULT_AUTH_LOST, 401, "session_expired");

async function requestOnce<T>(path: string, options: ApiOptions, auth: boolean): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form; // fetch sets the multipart boundary itself
  } else if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  const send = (token: string | null) =>
    doFetch(`${BASE_URL}${path}`, {
      method: options.method ?? (body ? "POST" : "GET"),
      headers: token ? { ...headers, Authorization: `Bearer ${token}` } : headers,
      body,
    });

  let used: string | null = null;
  if (auth) {
    // A cold start (or the background task after a restart) has no access token yet.
    if (!accessToken && !(await refreshSession())) throw sessionExpired();
    used = accessToken;
  }
  let response = await send(used);

  if (auth && response.status === 401) {
    // Another request may already have refreshed while this one was in flight.
    if (accessToken && accessToken !== used) {
      response = await send(accessToken);
    } else if (await refreshSession()) {
      response = await send(accessToken);
    } else {
      throw sessionExpired();
    }
  }

  if (!response.ok) {
    const error = await toError(response);
    if (isConsentRequiredError(error)) consentRequiredListener?.();
    throw error;
  }
  return (await readBody(response)) as T;
}

const isRetryable = (e: unknown) => e instanceof ApiError && (e.status === 0 || e.status >= 500);

/**
 * Call the Attendify API and return the parsed JSON (null for 204). Throws ApiError with the
 * server's `error.message` and `error.code`; network failures are ApiError with status 0.
 */
export async function apiFetch<T = any>(path: string, options: ApiOptions = {}): Promise<T> {
  const auth = options.auth ?? !PUBLIC_PATHS.includes(path);
  const retries = options.retries ?? (options.idempotencyKey ? 2 : 0);
  for (let attempt = 0; ; attempt++) {
    try {
      return await requestOnce<T>(path, options, auth);
    } catch (error) {
      if (attempt >= retries || !isRetryable(error)) throw error;
      await sleep(retryDelayMs * (attempt + 1));
    }
  }
}

/** POST /auth/logout with the refresh token (best effort), then forget the tokens. */
export async function revokeSession() {
  const refreshToken = await getRefreshToken();
  try {
    if (refreshToken) {
      await doFetch(`${BASE_URL}${API_CONFIG.ENDPOINTS.LOGOUT}`, {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
    }
  } catch {
    /* offline: the server revokes it when it expires; the local copy is wiped below */
  }
  await clearSession();
}

// Small stored flags used to start up offline.
export async function storeJson(key: string, value: unknown) {
  try {
    await SecureStore.setItemAsync(key, JSON.stringify(value), SECURE_BACKGROUND_OPTIONS);
  } catch {
    /* the in-memory state still applies */
  }
}

export async function loadJson<T>(key: string): Promise<T | null> {
  try {
    const raw = await SecureStore.getItemAsync(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}
