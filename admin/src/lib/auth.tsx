import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  api,
  clearTokens,
  currentRefreshToken,
  hasStoredSession,
  refreshAccess,
  setTokens,
  setUnauthorizedHandler,
} from "../api/client";
import type { LoginOut, Me, SignupOut } from "../api/types";
import { P } from "../api/types";
import { useToast } from "./toast";

export type LoginInput =
  | { email: string; password: string }
  | { org_slug: string; employee_code: string; password: string };

interface AuthState {
  me: Me | null;
  loading: boolean;
  /** True when the user may open the admin console at all (anything beyond plain self-service). */
  console: boolean;
  can: (permission: string) => boolean;
  /** True when the user has at least one of the permissions. */
  canAny: (...permissions: string[]) => boolean;
  login: (input: LoginInput) => Promise<void>;
  signup: (input: { org_name: string; name: string; email: string; password: string }) => Promise<void>;
  acceptInvite: (token: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  logoutAll: () => Promise<void>;
  /** Re-read /people/me (after changing modules, profile, consent...). */
  reload: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

const CONSOLE_PERMISSIONS: string[] = [
  P.viewTeam,
  P.viewOrg,
  P.approveTeam,
  P.approveOrg,
  P.peopleTeam,
  P.peopleManage,
  P.placesManage,
  P.policiesManage,
  P.export,
  P.audit,
  P.orgManage,
];

function deviceLabel(): string {
  const ua = navigator.userAgent;
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  return `Admin console (${browser})`;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState<boolean>(() => hasStoredSession());
  const qc = useQueryClient();
  const toast = useToast();
  const signedIn = useRef(false);

  const clear = useCallback(() => {
    clearTokens();
    signedIn.current = false;
    setMe(null);
    qc.clear();
  }, [qc]);

  const loadMe = useCallback(async () => {
    const m = await api<Me>("/people/me");
    signedIn.current = true;
    setMe(m);
  }, []);

  // The session cannot be recovered (refresh failed): drop to the login screen.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (signedIn.current) toast.error("Your session ended. Please sign in again.");
      clear();
    });
    return () => setUnauthorizedHandler(null);
  }, [clear, toast]);

  // On a reload the access token is gone; trade the stored refresh token for a new one.
  useEffect(() => {
    if (!hasStoredSession()) return;
    let cancelled = false;
    (async () => {
      try {
        if (await refreshAccess()) await loadMe();
      } catch {
        /* network trouble at boot: show the login screen, the stored token is kept for the next try */
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadMe]);

  const start = useCallback(
    async (out: LoginOut | SignupOut) => {
      qc.clear();
      setTokens(out);
      await loadMe();
    },
    [loadMe, qc],
  );

  const login = useCallback(
    async (input: LoginInput) => {
      const out = await api<LoginOut>("/auth/login", {
        method: "POST",
        auth: false,
        body: { ...input, device_label: deviceLabel() },
      });
      await start(out);
    },
    [start],
  );

  const signup = useCallback(
    async (input: { org_name: string; name: string; email: string; password: string }) => {
      const out = await api<SignupOut>("/orgs/signup", { method: "POST", auth: false, body: input });
      await start(out);
    },
    [start],
  );

  const acceptInvite = useCallback(
    async (token: string, password: string) => {
      const out = await api<LoginOut>("/auth/invite/accept", { method: "POST", auth: false, body: { token, password } });
      await start(out);
    },
    [start],
  );

  const logout = useCallback(async () => {
    const token = currentRefreshToken();
    if (token) {
      // Best effort: the server call is idempotent and the local session ends regardless.
      await api("/auth/logout", { method: "POST", auth: false, body: { refresh_token: token } }).catch(() => undefined);
    }
    clear();
  }, [clear]);

  const logoutAll = useCallback(async () => {
    await api("/auth/logout-all", { method: "POST" });
    clear();
  }, [clear]);

  const value = useMemo<AuthState>(() => {
    const perms = new Set(me?.permissions ?? []);
    return {
      me,
      loading,
      console: CONSOLE_PERMISSIONS.some((p) => perms.has(p)),
      can: (p) => perms.has(p),
      canAny: (...ps) => ps.some((p) => perms.has(p)),
      login,
      signup,
      acceptInvite,
      logout,
      logoutAll,
      reload: loadMe,
    };
  }, [me, loading, login, signup, acceptInvite, logout, logoutAll, loadMe]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
