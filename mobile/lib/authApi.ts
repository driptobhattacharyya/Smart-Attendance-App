// Sign-in, invite acceptance and password recovery (API v1 /auth/*).
// Employees are created by their company; there is no self-registration.
import { Platform } from "react-native";
import { apiFetch, saveTokens } from "./api";
import { API_CONFIG } from "./config";

export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  consent_required?: boolean;
  user?: { id: string; name: string; email: string };
};

const deviceLabel = () => `Attendify ${Platform.OS}`;

async function signIn(path: string, json: Record<string, unknown>): Promise<TokenResponse> {
  const data = await apiFetch<TokenResponse>(path, { json: { ...json, device_label: deviceLabel() } });
  await saveTokens(data);
  return data;
}

export const loginWithEmail = (email: string, password: string) =>
  signIn(API_CONFIG.ENDPOINTS.LOGIN, { email: email.trim(), password });

export const loginWithEmployeeCode = (orgSlug: string, employeeCode: string, password: string) =>
  signIn(API_CONFIG.ENDPOINTS.LOGIN, {
    org_slug: orgSlug.trim().toLowerCase(),
    employee_code: employeeCode.trim(),
    password,
  });

/** POST /auth/invite/accept: sets the password and signs in. */
export const acceptInvite = (token: string, password: string) =>
  signIn(API_CONFIG.ENDPOINTS.INVITE_ACCEPT, { token: token.trim(), password });

export const requestPasswordReset = (email: string) =>
  apiFetch<{ message: string }>(API_CONFIG.ENDPOINTS.PASSWORD_FORGOT, { json: { email: email.trim() } });

export const resetPassword = (token: string, newPassword: string) =>
  apiFetch<{ message: string }>(API_CONFIG.ENDPOINTS.PASSWORD_RESET, {
    json: { token: token.trim(), new_password: newPassword },
  });

/** The server enforces 10-128 characters. */
export const PASSWORD_MIN = 10;
export const PASSWORD_MAX = 128;

export function passwordProblem(password: string, confirm?: string): string | null {
  if (password.length < PASSWORD_MIN) return `Use at least ${PASSWORD_MIN} characters.`;
  if (password.length > PASSWORD_MAX) return `Use at most ${PASSWORD_MAX} characters.`;
  if (confirm !== undefined && password !== confirm) return "The two passwords do not match.";
  return null;
}
