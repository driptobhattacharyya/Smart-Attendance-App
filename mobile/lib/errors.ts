// API error format (architecture section 10):
//   {"error": {"code": "consent_required", "message": "...", "details": {}}}
// Validation errors are 422 with code "validation_error" and details.errors[] = {loc, msg, type}.

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, any>;
  /** Seconds from the Retry-After header (429). */
  retryAfter?: number;

  constructor(message: string, status: number, code = "error", details: Record<string, any> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/** Turn a response body into an ApiError. Accepts the v1 format, FastAPI's {detail} and junk. */
export function parseApiError(body: any, status: number, fallback?: string): ApiError {
  const err = body?.error;
  if (err && typeof err === "object") {
    let message = typeof err.message === "string" && err.message ? err.message : undefined;
    const details = err.details && typeof err.details === "object" ? err.details : {};
    if (!message && Array.isArray(details.errors)) {
      message = details.errors.map((d: any) => d?.msg).filter(Boolean).join("; ") || undefined;
    }
    return new ApiError(
      message ?? fallback ?? `Request failed (${status})`,
      status,
      typeof err.code === "string" ? err.code : "error",
      details
    );
  }
  const detail = body?.detail;
  if (typeof detail === "string") return new ApiError(detail, status);
  if (Array.isArray(detail)) {
    const message = detail.map((d: any) => d?.msg).filter(Boolean).join("; ") || `Request failed (${status})`;
    return new ApiError(message, status, "validation_error");
  }
  return new ApiError(fallback ?? `Request failed (${status})`, status);
}

export function networkError(): ApiError {
  return new ApiError("Cannot reach the server. Check your connection and try again.", 0, "network_error");
}

export const isApiError = (e: unknown): e is ApiError => e instanceof ApiError;
export const isNetworkError = (e: unknown) => isApiError(e) && e.status === 0;
export const isConsentRequiredError = (e: unknown) => isApiError(e) && e.code === "consent_required";

/** Message for the UI: the server's message when there is one, a generic one otherwise. */
export function errorMessage(e: unknown, fallback = "Something went wrong. Please try again."): string {
  if (isApiError(e)) {
    if (e.code === "rate_limited" && e.retryAfter) {
      const minutes = Math.ceil(e.retryAfter / 60);
      return `${e.message} Try again in ${minutes > 1 ? `${minutes} minutes` : "a minute"}.`;
    }
    return e.message || fallback;
  }
  return fallback;
}
