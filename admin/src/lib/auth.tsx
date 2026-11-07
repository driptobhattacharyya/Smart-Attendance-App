import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, getToken, setToken, setUnauthorizedHandler } from "./api";
import type { AuthOut, User } from "./types";
import { useToast } from "./toast";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (identifier: string, password: string) => Promise<User>;
  logout: () => void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(() => getToken() !== null);
  const qc = useQueryClient();
  const toast = useToast();

  const clear = useCallback(() => {
    setToken(null);
    setUser(null);
    qc.clear();
  }, [qc]);

  // Any 401 from the API means the session is gone.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser((u) => {
        if (u) toast.error("Your session expired. Please sign in again.");
        return null;
      });
      qc.clear();
    });
    return () => setUnauthorizedHandler(null);
  }, [qc, toast]);

  useEffect(() => {
    if (!getToken()) return;
    let cancelled = false;
    api<User>("/auth/me")
      .then((u) => !cancelled && setUser(u))
      .catch(() => !cancelled && setUser(null))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (identifier: string, password: string) => {
    const out = await api<AuthOut>("/auth/login", {
      method: "POST",
      body: { employee_id: identifier.trim(), password },
    });
    setToken(out.access_token);
    setUser(out.user);
    return out.user;
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, logout: clear }),
    [user, loading, login, clear],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
