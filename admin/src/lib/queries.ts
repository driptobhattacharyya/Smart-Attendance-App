import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useAuth } from "./auth";
import type { AttendanceRequest, Beacon, Office, User } from "./types";

export function useOffices() {
  return useQuery({ queryKey: ["offices"], queryFn: () => api<Office[]>("/offices"), staleTime: 60_000 });
}

/** Admins see every account; managers see their team. */
export function usePeople() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  return useQuery({
    queryKey: ["people", admin ? "admin" : "team"],
    queryFn: () => api<User[]>(admin ? "/admin/users" : "/manager/team"),
  });
}

export function useBeacons(officeId = "") {
  return useQuery({
    queryKey: ["beacons", officeId],
    queryFn: () =>
      api<Beacon[]>(`/beacons${officeId ? `?office_id=${encodeURIComponent(officeId)}` : ""}`),
  });
}

export function useRequests() {
  return useQuery({
    queryKey: ["requests"],
    queryFn: () => api<AttendanceRequest[]>("/manager/requests"),
  });
}

export function officeName(offices: Office[] | undefined, id: string | null | undefined): string {
  if (!id) return "—";
  return offices?.find((o) => o.id === id)?.name ?? "Unknown office";
}
