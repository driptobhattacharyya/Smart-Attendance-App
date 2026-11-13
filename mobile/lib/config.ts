// The API address comes from EXPO_PUBLIC_API_URL (see .env.example) and includes the version
// prefix, e.g. https://api.example.com/api/v1.
// On an Android emulator, "localhost" is the emulator itself: use http://10.0.2.2:8000/api/v1.
export const BASE_URL = (process.env.EXPO_PUBLIC_API_URL ?? "http://localhost:8000/api/v1").replace(/\/$/, "");

/**
 * Endpoints that exist today (auth, people, places, consent). The attendance and face
 * endpoints live in lib/attendanceApi.ts and lib/faceApi.ts so they are easy to adjust.
 */
export const API_CONFIG = {
  BASE_URL,
  ENDPOINTS: {
    LOGIN: "/auth/login",
    REFRESH: "/auth/refresh",
    LOGOUT: "/auth/logout",
    INVITE_ACCEPT: "/auth/invite/accept",
    PASSWORD_FORGOT: "/auth/password/forgot",
    PASSWORD_RESET: "/auth/password/reset",
    ME: "/people/me",
    OFFICE: (id: string) => `/places/offices/${encodeURIComponent(id)}`,
    DESK_DEVICES_MINE: "/places/desk-devices/mine",
    CONSENT_TEXT: "/consent/text",
    CONSENT: "/auth/consent",
    CONSENT_WITHDRAW: "/auth/consent/withdraw",
    // Not in api-v1.md yet (architecture section 9 requires an export of own data).
    MY_EXPORT: "/people/me/export",
  },
} as const;

/** Paths that never carry (or need) an access token. */
export const PUBLIC_PATHS: string[] = [
  API_CONFIG.ENDPOINTS.LOGIN,
  API_CONFIG.ENDPOINTS.REFRESH,
  API_CONFIG.ENDPOINTS.LOGOUT,
  API_CONFIG.ENDPOINTS.INVITE_ACCEPT,
  API_CONFIG.ENDPOINTS.PASSWORD_FORGOT,
  API_CONFIG.ENDPOINTS.PASSWORD_RESET,
];
