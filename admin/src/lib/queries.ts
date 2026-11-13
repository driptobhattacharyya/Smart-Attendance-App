import { useEffect, useState } from "react";
import { keepPreviousData, useInfiniteQuery, useQueries, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Department, DeskDevice, Office, Page, User } from "../api/types";

export function useOffices() {
  return useQuery({ queryKey: ["offices"], queryFn: () => api<Office[]>("/places/offices"), staleTime: 60_000 });
}

export function useDepartments() {
  return useQuery({ queryKey: ["departments"], queryFn: () => api<Department[]>("/departments"), staleTime: 60_000 });
}

/** Pass `pollMs` while something waits on the list (e.g. a computer being paired). */
export function useDeskDevices(pollMs: number | false = false, enabled = true) {
  return useQuery({
    queryKey: ["desk-devices"],
    enabled,
    queryFn: () => api<DeskDevice[]>("/places/desk-devices"),
    refetchInterval: pollMs,
  });
}

export interface PeopleFilters {
  q?: string;
  status?: string;
  department_id?: string;
  office_id?: string;
  manager_id?: string;
  role_key?: string;
}

/** Cursor-paginated people list; `fetchNextPage` loads the next 50. */
export function usePeopleList(filters: PeopleFilters, limit = 50) {
  return useInfiniteQuery({
    queryKey: ["people", "list", filters, limit],
    initialPageParam: "" as string,
    queryFn: ({ pageParam }) => api<Page<User>>("/people", { query: { ...filters, limit, cursor: pageParam } }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  });
}

/** Just the total-ish signal used by onboarding: is there more than one person? */
export function usePeopleCount(enabled: boolean) {
  return useQuery({
    queryKey: ["people", "count"],
    enabled,
    queryFn: async () => {
      const p = await api<Page<User>>("/people", { query: { limit: 2 } });
      return p.items.length;
    },
  });
}

export function usePerson(id: string | null | undefined) {
  return useQuery({
    queryKey: ["people", "one", id],
    enabled: !!id,
    retry: false,
    staleTime: 60_000,
    queryFn: () => api<User>(`/people/${id}`),
  });
}

/** Names for a set of user ids (cached per person). Ids outside the viewer's scope resolve to undefined. */
export function usePeopleNames(ids: (string | null | undefined)[]): Map<string, string | undefined> {
  const unique = [...new Set(ids.filter((id): id is string => !!id))];
  const results = useQueries({
    queries: unique.map((id) => ({
      queryKey: ["people", "one", id],
      retry: false,
      staleTime: 60_000,
      queryFn: () => api<User>(`/people/${id}`),
    })),
  });
  return new Map(unique.map((id, i) => [id, results[i]?.data?.name]));
}

/** Debounce a fast-changing value (search boxes) before it reaches the query key. */
export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function officeName(offices: Office[] | undefined, id: string | null | undefined): string {
  if (!id) return "—";
  return offices?.find((o) => o.id === id)?.name ?? "Unknown office";
}

export function departmentName(deps: Department[] | undefined, id: string | null | undefined): string {
  if (!id) return "—";
  return deps?.find((d) => d.id === id)?.name ?? "Unknown";
}
