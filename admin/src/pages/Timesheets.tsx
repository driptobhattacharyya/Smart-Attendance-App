import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CalendarDays, ChevronLeft, ChevronRight, Download } from "lucide-react";
import { api, download } from "../lib/api";
import type { AttendanceReport, DaySummary, Session } from "../lib/types";
import {
  addDays,
  dateRange,
  fmtDay,
  fmtRange,
  fmtTime,
  formatMinutes,
  isoDate,
  parseDate,
  startOfWeek,
} from "../lib/format";
import { useToast } from "../lib/toast";
import { Avatar, Badge, Card, EmptyState, ErrorState, PageHeader, Skeleton } from "../ui/misc";
import { Button, IconButton } from "../ui/Button";
import { Input, SearchInput } from "../ui/form";
import { Dialog } from "../ui/Dialog";
import { cx } from "../ui/cx";
import { SessionTimeline } from "./SessionTimeline";

const MAX_DAYS = 93;

interface Selected {
  report: AttendanceReport;
  day: DaySummary;
}

function awayOf(day: DaySummary): number {
  return day.sessions.reduce((n, s) => n + (s.away_minutes || 0), 0);
}

export function Timesheets() {
  const toast = useToast();
  const [start, setStart] = useState(() => isoDate(startOfWeek(new Date())));
  const [end, setEnd] = useState(() => isoDate(addDays(startOfWeek(new Date()), 6)));
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Selected | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const days = useMemo(() => (start <= end ? dateRange(start, end) : []), [start, end]);
  const tooLong = days.length > MAX_DAYS;
  const valid = days.length > 0 && !tooLong;

  const q = useQuery({
    queryKey: ["attendance", start, end],
    queryFn: () => api<AttendanceReport[]>(`/manager/attendance?start=${start}&end=${end}`),
    enabled: valid,
  });

  const exportCsv = useMutation({
    mutationFn: () => download(`/manager/attendance/export.csv?start=${start}&end=${end}`, `attendance_${start}_${end}.csv`),
    onSuccess: () => toast.success("CSV downloaded"),
    onError: (e: Error) => toast.error(e.message),
  });

  function shift(dir: -1 | 1) {
    const span = Math.max(days.length, 1);
    setStart(isoDate(addDays(parseDate(start), dir * span)));
    setEnd(isoDate(addDays(parseDate(end), dir * span)));
  }
  function thisWeek() {
    const s = startOfWeek(new Date());
    setStart(isoDate(s));
    setEnd(isoDate(addDays(s, 6)));
  }

  const reports = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (q.data ?? []).filter(
      (r) => !term || r.name.toLowerCase().includes(term) || r.employee_id.toLowerCase().includes(term),
    );
  }, [q.data, search]);

  const dayTotals = useMemo(() => {
    const totals = new Map<string, number>();
    reports.forEach((r) => r.days.forEach((d) => totals.set(d.date, (totals.get(d.date) ?? 0) + d.total_minutes)));
    return totals;
  }, [reports]);
  const grand = reports.reduce((n, r) => n + r.total_minutes, 0);
  const today = isoDate(new Date());

  function openCell(report: AttendanceReport, day: DaySummary) {
    setSelected({ report, day });
    setSessionId(day.sessions.length === 1 ? day.sessions[0].id : null);
  }

  return (
    <>
      <PageHeader
        title="Timesheets"
        description="Worked time per person per day. Time away from the desk is shown beneath, and is not counted."
        actions={
          <Button
            onClick={() => exportCsv.mutate()}
            loading={exportCsv.isPending}
            disabled={!valid}
            icon={<Download className="size-4" />}
          >
            Export CSV
          </Button>
        }
      />

      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex items-center gap-1">
            <IconButton label="Previous period" onClick={() => shift(-1)} className="border border-line-strong bg-surface shadow-card">
              <ChevronLeft className="size-4" />
            </IconButton>
            <IconButton label="Next period" onClick={() => shift(1)} className="border border-line-strong bg-surface shadow-card">
              <ChevronRight className="size-4" />
            </IconButton>
          </div>
          <label className="flex flex-col gap-1 text-xs text-muted">
            From
            <Input type="date" value={start} max={end} onChange={(e) => e.target.value && setStart(e.target.value)} className="w-40" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            To
            <Input type="date" value={end} min={start} onChange={(e) => e.target.value && setEnd(e.target.value)} className="w-40" />
          </label>
          <Button onClick={thisWeek}>This week</Button>
        </div>
        <SearchInput value={search} onChange={setSearch} placeholder="Search name or ID" className="lg:w-64" />
      </div>
      {tooLong && <p className="mb-3 text-sm text-danger">Pick a range of at most {MAX_DAYS} days.</p>}

      <Card>
        {!valid ? (
          <EmptyState icon={<CalendarDays />} title="Choose a valid date range" description="The start date must be on or before the end date." />
        ) : q.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 7 }, (_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} feature="Timesheets" />
        ) : reports.length === 0 ? (
          <EmptyState
            icon={<CalendarDays />}
            title={q.data?.length ? "No one matches that search" : "No people in this report"}
            description={q.data?.length ? undefined : "Active people in your team appear here once they are added in People."}
          />
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-max border-separate border-spacing-0 text-sm">
              <thead>
                <tr>
                  <th className="sticky left-0 z-10 min-w-48 border-b border-line bg-surface px-4 py-2.5 text-left text-xs font-medium tracking-wide text-muted uppercase">
                    Person · {fmtRange(start, end)}
                  </th>
                  {days.map((d) => {
                    const weekend = [0, 6].includes(parseDate(d).getDay());
                    return (
                      <th
                        key={d}
                        scope="col"
                        className={cx(
                          "min-w-[76px] border-b border-line px-2 py-2 text-center text-xs font-medium",
                          weekend ? "text-subtle" : "text-muted",
                          d === today && "text-accent-text",
                        )}
                      >
                        <span className="block uppercase">{fmtDay(d, { weekday: "short" })}</span>
                        <span className="block text-[11px] font-normal tabular-nums">{fmtDay(d, { day: "numeric", month: "short" })}</span>
                      </th>
                    );
                  })}
                  <th scope="col" className="border-b border-l border-line px-4 py-2.5 text-right text-xs font-medium tracking-wide text-muted uppercase">
                    Total
                  </th>
                </tr>
              </thead>
              <tbody>
                {reports.map((r) => {
                  const byDate = new Map(r.days.map((d) => [d.date, d]));
                  return (
                    <tr key={r.employee_id} className="group">
                      <th
                        scope="row"
                        className="sticky left-0 z-10 border-b border-line bg-surface px-4 py-2 text-left font-normal group-hover:bg-surface-2"
                      >
                        <div className="flex items-center gap-2.5">
                          <Avatar name={r.name} size={28} />
                          <div className="min-w-0">
                            <p className="max-w-36 truncate font-medium">{r.name}</p>
                            <p className="text-xs text-muted">{r.employee_id}</p>
                          </div>
                        </div>
                      </th>
                      {days.map((date) => {
                        const day = byDate.get(date);
                        const away = day ? awayOf(day) : 0;
                        const has = !!day && (day.sessions.length > 0 || day.on_leave);
                        const pct = day ? Math.min(day.total_minutes / 480, 1) * 24 : 0;
                        return (
                          <td key={date} className="border-b border-line p-0.5 group-hover:bg-surface-2/60">
                            {has && day ? (
                              <button
                                type="button"
                                onClick={() => openCell(r, day)}
                                aria-label={`${r.name}, ${fmtDay(date, { weekday: "long", day: "numeric", month: "long" })}: ${
                                  day.on_leave && !day.total_minutes ? "on leave" : formatMinutes(day.total_minutes, "no time")
                                }`}
                                className="flex h-12 w-full flex-col items-center justify-center rounded-md transition-shadow hover:ring-1 hover:ring-accent"
                                style={{ background: pct ? `color-mix(in srgb, var(--accent) ${pct}%, transparent)` : undefined }}
                              >
                                {day.on_leave && !day.total_minutes ? (
                                  <Badge tone="violet">Leave</Badge>
                                ) : (
                                  <>
                                    <span className="font-medium tabular-nums">{formatMinutes(day.total_minutes, "0m")}</span>
                                    {away > 0 && <span className="text-[11px] leading-none text-warn tabular-nums">{formatMinutes(away)} away</span>}
                                  </>
                                )}
                              </button>
                            ) : (
                              <span className="flex h-12 items-center justify-center text-subtle">·</span>
                            )}
                          </td>
                        );
                      })}
                      <td className="border-b border-l border-line px-4 py-2 text-right font-semibold tabular-nums">
                        {formatMinutes(r.total_minutes, "0m")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row" className="sticky left-0 z-10 bg-surface-2 px-4 py-2.5 text-left text-xs font-medium tracking-wide text-muted uppercase">
                    Team total
                  </th>
                  {days.map((d) => (
                    <td key={d} className="bg-surface-2 px-2 py-2.5 text-center text-xs font-medium text-muted tabular-nums">
                      {formatMinutes(dayTotals.get(d))}
                    </td>
                  ))}
                  <td className="border-l border-line bg-surface-2 px-4 py-2.5 text-right font-semibold tabular-nums">{formatMinutes(grand, "0m")}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </Card>

      <Dialog
        open={!!selected}
        onClose={() => setSelected(null)}
        variant="side"
        title={selected?.report.name ?? ""}
        description={selected ? fmtDay(selected.day.date, { weekday: "long", day: "numeric", month: "long", year: "numeric" }) : undefined}
      >
        {selected && <DayPanel selected={selected} sessionId={sessionId} onPick={setSessionId} />}
      </Dialog>
    </>
  );
}

function DayPanel({
  selected,
  sessionId,
  onPick,
}: {
  selected: Selected;
  sessionId: string | null;
  onPick: (id: string | null) => void;
}) {
  const { day } = selected;
  const away = awayOf(day);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="success">{formatMinutes(day.total_minutes, "0m")} counted</Badge>
        {away > 0 && <Badge tone="warn">{formatMinutes(away)} away from desk</Badge>}
        {day.on_leave && <Badge tone="violet">On leave</Badge>}
      </div>

      {day.sessions.length === 0 ? (
        <p className="text-sm text-muted">No check-ins on this day.</p>
      ) : (
        <div>
          <h3 className="mb-2 text-xs font-medium tracking-wide text-muted uppercase">Sessions</h3>
          <ul className="space-y-2">
            {day.sessions.map((s: Session) => {
              const active = s.id === sessionId;
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    aria-pressed={active}
                    onClick={() => onPick(active ? null : s.id)}
                    className={cx(
                      "flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
                      active ? "border-accent bg-accent-soft" : "border-line hover:bg-surface-2",
                    )}
                  >
                    <span>
                      <span className="block text-sm font-medium tabular-nums">
                        {fmtTime(s.started_at)} – {s.ended_at ? fmtTime(s.ended_at) : "now"}
                      </span>
                      <span className="block text-xs text-muted">
                        {s.source === "request" ? "Approved correction" : s.ended_at ? "From the app" : "In progress"}
                      </span>
                    </span>
                    <span className="text-right">
                      <span className="block text-sm font-semibold tabular-nums">{formatMinutes(s.minutes, "0m")}</span>
                      {s.away_minutes > 0 && <span className="block text-xs text-warn tabular-nums">{formatMinutes(s.away_minutes)} away</span>}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {sessionId && (
        <div>
          <h3 className="mb-3 text-xs font-medium tracking-wide text-muted uppercase">Timeline</h3>
          <SessionTimeline key={sessionId} sessionId={sessionId} />
        </div>
      )}
    </div>
  );
}
