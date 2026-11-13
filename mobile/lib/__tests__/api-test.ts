const mockStore = new Map<string, string>();
jest.mock("expo-secure-store", () => ({
  AFTER_FIRST_UNLOCK: 0,
  getItemAsync: jest.fn(async (k: string) => mockStore.get(k) ?? null),
  setItemAsync: jest.fn(async (k: string, v: string) => {
    mockStore.set(k, v);
  }),
  deleteItemAsync: jest.fn(async (k: string) => {
    mockStore.delete(k);
  }),
}));
let mockUuidCounter = 0;
jest.mock("expo-crypto", () => ({ randomUUID: () => `uuid-${++mockUuidCounter}` }));

import {
  apiFetch,
  getAccessToken,
  newIdempotencyKey,
  REFRESH_TOKEN_KEY,
  refreshSession,
  saveTokens,
  setAccessToken,
  setAuthLostListener,
  setConsentRequiredListener,
  setRetryDelay,
} from "../api";
import { ApiError, errorMessage, networkError, parseApiError } from "../errors";

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k: string) => headers[k] ?? null },
    json: async () => body,
  }) as unknown as Response;

const fetchMock = jest.fn();
(global as any).fetch = fetchMock;

const calls = (path: string) => fetchMock.mock.calls.filter(([url]) => String(url).endsWith(path));

beforeEach(() => {
  mockStore.clear();
  fetchMock.mockReset();
  setAccessToken(null);
  setAuthLostListener(null);
  setConsentRequiredListener(null);
  setRetryDelay(0);
});

describe("error parsing", () => {
  it("reads the v1 error format", () => {
    const err = parseApiError({ error: { code: "consent_required", message: "Accept the notice", details: { a: 1 } } }, 403);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("consent_required");
    expect(err.message).toBe("Accept the notice");
    expect(err.status).toBe(403);
    expect(err.details).toEqual({ a: 1 });
  });

  it("joins validation messages when there is no message", () => {
    const err = parseApiError(
      { error: { code: "validation_error", message: "", details: { errors: [{ msg: "too short" }, { msg: "bad email" }] } } },
      422
    );
    expect(err.code).toBe("validation_error");
    expect(err.message).toBe("too short; bad email");
  });

  it("falls back for FastAPI detail and junk bodies", () => {
    expect(parseApiError({ detail: "Nope" }, 400).message).toBe("Nope");
    expect(parseApiError(null, 502).message).toBe("Request failed (502)");
    expect(parseApiError("html", 500).code).toBe("error");
  });

  it("builds user messages", () => {
    const limited = new ApiError("Too many attempts.", 429, "rate_limited");
    limited.retryAfter = 600;
    expect(errorMessage(limited)).toContain("10 minutes");
    expect(errorMessage(networkError())).toMatch(/Cannot reach/);
    expect(errorMessage(new Error("x"), "fallback")).toBe("fallback");
  });

  it("surfaces error.message and error.code from a failed request", async () => {
    setAccessToken("a");
    fetchMock.mockResolvedValueOnce(json(403, { error: { code: "forbidden", message: "Not allowed", details: {} } }));
    await expect(apiFetch("/people/me")).rejects.toMatchObject({ message: "Not allowed", code: "forbidden", status: 403 });
  });

  it("tells the consent listener about consent_required", async () => {
    const listener = jest.fn();
    setConsentRequiredListener(listener);
    setAccessToken("a");
    fetchMock.mockResolvedValueOnce(json(403, { error: { code: "consent_required", message: "Consent required" } }));
    await expect(apiFetch("/attendance/status")).rejects.toMatchObject({ code: "consent_required" });
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe("token refresh", () => {
  it("refreshes ONCE for many parallel 401s, then retries them all", async () => {
    await saveTokens({ access_token: "old", refresh_token: "rt_1" });
    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      if (String(url).endsWith("/auth/refresh")) {
        return json(200, { access_token: "new", refresh_token: "rt_2", token_type: "bearer", expires_in: 900 });
      }
      const auth = (init.headers as Record<string, string>).Authorization;
      return auth === "Bearer new" ? json(200, { ok: true }) : json(401, { error: { code: "invalid_token", message: "expired" } });
    });

    const results = await Promise.all([apiFetch("/a"), apiFetch("/b"), apiFetch("/c")]);

    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    expect(calls("/auth/refresh")).toHaveLength(1);
    expect(JSON.parse(calls("/auth/refresh")[0][1].body)).toEqual({ refresh_token: "rt_1" });
    expect(getAccessToken()).toBe("new");
    expect(mockStore.get(REFRESH_TOKEN_KEY)).toBe("rt_2"); // rotated token saved
  });

  it("shares one in-flight refresh between direct callers", async () => {
    await saveTokens({ access_token: "x", refresh_token: "rt_1" });
    fetchMock.mockResolvedValue(json(200, { access_token: "n", refresh_token: "rt_2" }));
    const [a, b] = await Promise.all([refreshSession(), refreshSession()]);
    expect(a && b).toBe(true);
    expect(calls("/auth/refresh")).toHaveLength(1);
  });

  it("gets an access token first when only the refresh token survived (cold start / background task)", async () => {
    mockStore.set(REFRESH_TOKEN_KEY, "rt_1");
    fetchMock.mockImplementation(async (url: string) =>
      String(url).endsWith("/auth/refresh")
        ? json(200, { access_token: "fresh", refresh_token: "rt_2" })
        : json(200, { done: true })
    );
    await expect(apiFetch("/people/me")).resolves.toEqual({ done: true });
    expect(calls("/people/me")[0][1].headers.Authorization).toBe("Bearer fresh");
  });

  it("ends the session with a message when the refresh token is rejected", async () => {
    await saveTokens({ access_token: "old", refresh_token: "rt_1" });
    const lost = jest.fn();
    setAuthLostListener(lost);
    fetchMock.mockImplementation(async (url: string) =>
      String(url).endsWith("/auth/refresh")
        ? json(401, { error: { code: "token_reuse_detected", message: "reuse" } })
        : json(401, { error: { code: "invalid_token", message: "expired" } })
    );

    await expect(apiFetch("/people/me")).rejects.toMatchObject({ code: "session_expired", status: 401 });

    expect(lost).toHaveBeenCalledTimes(1);
    expect(lost.mock.calls[0][0]).toMatch(/security/);
    expect(getAccessToken()).toBeNull();
    expect(mockStore.has(REFRESH_TOKEN_KEY)).toBe(false);
  });

  it("keeps the session when the refresh fails because the network is down", async () => {
    await saveTokens({ access_token: "old", refresh_token: "rt_1" });
    const lost = jest.fn();
    setAuthLostListener(lost);
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).endsWith("/auth/refresh")) throw new TypeError("Network request failed");
      return json(401, { error: { code: "invalid_token", message: "expired" } });
    });
    await expect(apiFetch("/people/me")).rejects.toMatchObject({ status: 0, code: "network_error" });
    expect(lost).not.toHaveBeenCalled();
    expect(mockStore.get(REFRESH_TOKEN_KEY)).toBe("rt_1");
  });

  it("does not send a token or refresh for public paths", async () => {
    fetchMock.mockResolvedValueOnce(json(401, { error: { code: "invalid_credentials", message: "Wrong password" } }));
    await expect(apiFetch("/auth/login", { json: { email: "a", password: "b" } })).rejects.toMatchObject({
      code: "invalid_credentials",
    });
    expect(calls("/auth/refresh")).toHaveLength(0);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });
});

describe("idempotency", () => {
  it("generates a different key per attempt", () => {
    expect(newIdempotencyKey()).not.toBe(newIdempotencyKey());
  });

  it("reuses the same Idempotency-Key when a network error is retried", async () => {
    setAccessToken("a");
    const key = newIdempotencyKey();
    fetchMock
      .mockRejectedValueOnce(new TypeError("Network request failed"))
      .mockResolvedValueOnce(json(503, { error: { code: "unavailable", message: "later" } }))
      .mockResolvedValueOnce(json(200, { session: { id: "s1" } }));

    const result = await apiFetch("/attendance/check-in", { json: { mode: "onsite" }, idempotencyKey: key });

    expect(result).toEqual({ session: { id: "s1" } });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const keys = fetchMock.mock.calls.map(([, init]) => init.headers["Idempotency-Key"]);
    expect(keys).toEqual([key, key, key]);
  });

  it("does not retry 4xx answers", async () => {
    setAccessToken("a");
    fetchMock.mockResolvedValue(json(403, { error: { code: "forbidden", message: "Mock location detected" } }));
    await expect(
      apiFetch("/attendance/check-in", { json: {}, idempotencyKey: newIdempotencyKey() })
    ).rejects.toMatchObject({ message: "Mock location detected" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the key across a 401 refresh retry", async () => {
    await saveTokens({ access_token: "old", refresh_token: "rt_1" });
    const key = newIdempotencyKey();
    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      if (String(url).endsWith("/auth/refresh")) return json(200, { access_token: "new", refresh_token: "rt_2" });
      return (init.headers as any).Authorization === "Bearer new" ? json(200, {}) : json(401, { error: { code: "invalid_token", message: "x" } });
    });
    await apiFetch("/attendance/heartbeat", { json: {}, idempotencyKey: key });
    const sent = calls("/attendance/heartbeat").map(([, init]) => init.headers["Idempotency-Key"]);
    expect(sent).toEqual([key, key]);
  });

  it("does not retry requests without a key", async () => {
    setAccessToken("a");
    fetchMock.mockRejectedValue(new TypeError("Network request failed"));
    await expect(apiFetch("/people/me")).rejects.toMatchObject({ status: 0 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
