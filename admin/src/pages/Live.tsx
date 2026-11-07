import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Users } from "lucide-react";
import { api } from "../lib/api";
import type { PresenceRow, PresenceStatus } from "../lib/types";
import { fmtTime, formatMinutes, signalLevel, STATUS_LABEL, timeAgo } from "../lib/format";
import { officeName, useOffices } from "../lib/queries";
import { Avatar, Card, ChipGroup, EmptyState, ErrorState, PageHeader, SignalBars, StatusBadge, Table, TableSkeleton, Td, Th } from "../ui/misc";
import { Button } from "../ui/Button";
import { SearchInput, Select } from "../ui/form";

type Filter = "all" | PresenceStatus;
const FILTERS: Filter[] = ["all", "present", "away", "stale", "outside", "not_checked_in", "on_leave"];

export function Live() {
  const [params, setParams] = useSearchParams();
  const initial = params.get("status") as Filter | null;
  const [filter, setFilter] = useState<Filter>(initial && FILTERS.includes(initial) ? initial : "all");
  const [office, setOffice] = useState("");
  const [search, setSearch] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const offices = useOffices();

  const q = useQuery({
    queryKey: ["presence", office],
    queryFn: () => api<PresenceRow[]>(`/manager/presence${office ? `?office_id=${encodeURIComponent(office)}` : ""}`),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false, // pauses while the tab is hidden
    staleTime: 10_000,
  });

  // Keep "x min ago" honest between refreshes.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(t);
  }, []);

  const setFilterAndUrl = (f: Filter) => {
    setFilter(f);
    setParams(f === "all" ? {} : { status: f }, { replace: true });
  };

  const rows = q.data;
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: rows?.length ?? 0 };
    rows?.forEach((r) => (c[r.status] = (c[r.status] ?? 0) + 1));
    return c;
  }, [rows]);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (rows ?? []).filter(
      (r) =>
        (filter === "all" || r.status === filter) &&
        (!term || r.name.toLowerCase().includes(term) || r.employee_id.toLowerCase().includes(term)),
    );
  }, [rows, filter, search]);

  const updated = q.dataUpdatedAt ? timeAgo(new Date(q.dataUpdatedAt).toISOString(), now) : null;

  return (
    <>
      <PageHeader
        title="Live"
        description="Who is at their desk right now. Refreshes every 30 seconds while this tab is open."
        actions={
          <>
            {updated && <span className="text-xs text-muted">Updated {updated}</span>}
            <Button
              onClick={() => q.refetch()}
              loading={q.isFetching && !q.isLoading}
              icon={<RefreshCw className="size-4" />}
            >
              Refresh
            </Button>
          </>
        }
      />

      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <ChipGroup
          label="Filter by status"
          value={filter}
          onChange={setFilterAndUrl}
          options={FILTERS.map((f) => ({
            value: f,
            label: f === "all" ? "Everyone" : STATUS_LABEL[f],
            count: q.data ? (counts[f] ?? 0) : undefined,
          }))}
        />
        <div className="flex flex-col gap-2 sm:flex-row">
          {(offices.data?.length ?? 0) > 1 && (
            <Select aria-label="Office" value={office} onChange={(e) => setOffice(e.target.value)} className="sm:w-44">
              <option value="">All offices</option>
              {offices.data?.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </Select>
          )}
          <SearchInput value={search} onChange={setSearch} placeholder="Search name or ID" className="sm:w-60" />
        </div>
      </div>

      <Card>
        {q.isLoading ? (
          <TableSkeleton rows={8} />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} feature="The live board" />
        ) : visible.length === 0 ? (
          <EmptyState
            icon={<Users />}
            title={rows?.length ? "No one matches these filters" : "No people to show yet"}
            description={
              rows?.length
                ? "Try a different status or clear the search."
                : "Add people to an office and they will appear here as soon as they check in."
            }
            action={
              rows?.length ? (
                <Button
                  onClick={() => {
                    setFilterAndUrl("all");
                    setSearch("");
                  }}
                >
                  Clear filters
                </Button>
              ) : undefined
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Person</Th>
                <Th>Status</Th>
                <Th className="max-md:hidden">Since</Th>
                <Th className="max-sm:hidden" align="right">
                  Worked today
                </Th>
                <Th className="max-lg:hidden">Desk signal</Th>
                <Th className="max-lg:hidden">Office</Th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.employee_id} className="hover:bg-surface-2/50">
                  <Td>
                    <div className="flex items-center gap-3">
                      <Avatar name={r.name} />
                      <div className="min-w-0">
                        <p className="truncate font-medium">{r.name}</p>
                        <p className="text-xs text-muted">
                          {r.employee_id}
                          <span className="sm:hidden"> · {formatMinutes(r.today_minutes, "0m")} today</span>
                        </p>
                      </div>
                    </div>
                  </Td>
                  <Td>
                    <StatusBadge status={r.status} />
                    {r.status === "stale" && r.last_heartbeat_at && (
                      <p className="mt-0.5 text-xs text-muted">Last seen {timeAgo(r.last_heartbeat_at, now)}</p>
                    )}
                  </Td>
                  <Td className="text-muted tabular-nums max-md:hidden">{r.since ? fmtTime(r.since) : "—"}</Td>
                  <Td align="right" className="tabular-nums max-sm:hidden">
                    {formatMinutes(r.today_minutes)}
                  </Td>
                  <Td className="max-lg:hidden">
                    {r.checked_in ? <SignalBars level={signalLevel(r.best_rssi)} rssi={r.best_rssi} /> : <span className="text-muted">—</span>}
                  </Td>
                  <Td className="text-muted max-lg:hidden">{officeName(offices.data, r.office_id)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
