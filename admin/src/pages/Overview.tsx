import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";
import {
  Bluetooth,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  Clock,
  Monitor,
  ScanFace,
  TriangleAlert,
} from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { Dashboard, PresenceStatus } from "../lib/types";
import { fmtDay, fmtTime, formatMinutes, STATUS_HELP, STATUS_LABEL } from "../lib/format";
import { Avatar, Card, CardHeader, EmptyState, ErrorState, PageHeader, Skeleton, STATUS_TONE } from "../ui/misc";
import type { Tone } from "../ui/misc";
import { cx } from "../ui/cx";

const CARD_STATUSES: PresenceStatus[] = ["present", "away", "stale", "not_checked_in", "on_leave"];

const DOT: Record<Tone, string> = {
  success: "bg-success",
  warn: "bg-warn",
  danger: "bg-danger",
  info: "bg-info",
  violet: "bg-violet",
  accent: "bg-accent",
  neutral: "bg-subtle",
};

function StatCard({ status, value, total }: { status: PresenceStatus; value: number; total: number }) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  return (
    <Link
      to={`/live?status=${status}`}
      title={STATUS_HELP[status]}
      className="group rounded-xl border border-line bg-surface p-4 max-sm:last:col-span-2 shadow-card transition-colors hover:border-line-strong hover:bg-surface-2/50"
    >
      <div className="flex items-center gap-2 text-[13px] text-muted">
        <span className={cx("size-2 rounded-full", DOT[STATUS_TONE[status]])} aria-hidden />
        {STATUS_LABEL[status]}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-3xl font-semibold tracking-tight tabular-nums">{value}</span>
        <span className="text-xs text-muted tabular-nums">{pct}%</span>
      </div>
    </Link>
  );
}

function ChartTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload as { date: string; worked: number; away: number; people: number };
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-pop">
      <p className="mb-1 font-medium">{fmtDay(String(label), { weekday: "long", day: "numeric", month: "short" })}</p>
      <p className="text-muted">
        <span className="mr-1.5 inline-block size-2 rounded-full bg-accent" />
        Worked <span className="font-medium text-fg tabular-nums">{row.worked.toFixed(1)}h</span>
      </p>
      <p className="text-muted">
        <span className="mr-1.5 inline-block size-2 rounded-full bg-warn" />
        Away <span className="font-medium text-fg tabular-nums">{row.away.toFixed(1)}h</span>
      </p>
      <p className="mt-1 text-muted">{row.people} people checked in</p>
    </div>
  );
}

function Callout({
  to,
  icon,
  count,
  title,
  hint,
}: {
  to: string;
  icon: React.ReactNode;
  count: number;
  title: string;
  hint: string;
}) {
  return (
    <Link
      to={to}
      className="group flex items-center gap-3 rounded-lg border border-line px-3 py-2.5 transition-colors hover:bg-surface-2"
    >
      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-warn-soft text-warn [&_svg]:size-4">
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">
          <span className="tabular-nums">{count}</span> {title}
        </span>
        <span className="block truncate text-xs text-muted">{hint}</span>
      </span>
      <ChevronRight className="size-4 text-subtle transition-transform group-hover:translate-x-0.5" aria-hidden />
    </Link>
  );
}

export function Overview() {
  const { user } = useAuth();
  const q = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api<Dashboard>("/manager/dashboard"),
    refetchInterval: 60_000,
  });
  const d = q.data;

  const chart = d?.daily.map((x) => ({
    date: x.date,
    worked: +(x.worked_minutes / 60).toFixed(2),
    away: +(x.away_minutes / 60).toFixed(2),
    people: x.people,
  }));

  const issues = d
    ? [
        d.pending_requests > 0 && (
          <Callout
            key="req"
            to="/requests"
            icon={<ClipboardList />}
            count={d.pending_requests}
            title={d.pending_requests === 1 ? "request waiting for review" : "requests waiting for review"}
            hint="Corrections and leave from your team"
          />
        ),
        d.employees_without_face > 0 && (
          <Callout
            key="face"
            to="/people?setup=no-face"
            icon={<ScanFace />}
            count={d.employees_without_face}
            title={d.employees_without_face === 1 ? "person without a face scan" : "people without a face scan"}
            hint="They cannot check in until they enrol in the app"
          />
        ),
        d.employees_without_beacon > 0 && (
          <Callout
            key="bt"
            to="/people?setup=no-beacon"
            icon={<Bluetooth />}
            count={d.employees_without_beacon}
            title={d.employees_without_beacon === 1 ? "person without a beacon" : "people without a beacon"}
            hint="Assign each a desk beacon, or add a shared one for the office"
          />
        ),
        !!d.unpaired_desk_apps && user?.role === "admin" && (
          <Callout
            key="dk"
            to="/beacons?filter=unpaired"
            icon={<Monitor />}
            count={d.unpaired_desk_apps}
            title={d.unpaired_desk_apps === 1 ? "office computer not paired" : "office computers not paired"}
            hint="Pair them with Attendify Desk so their desks count as present"
          />
        ),
        !!d.unassigned_beacons && user?.role === "admin" && (
          <Callout
            key="ub"
            to="/beacons?filter=unassigned"
            icon={<TriangleAlert />}
            count={d.unassigned_beacons}
            title={d.unassigned_beacons === 1 ? "beacon with no owner" : "beacons with no owner"}
            hint="Assign them to a person or keep as shared zone tags"
          />
        ),
      ].filter(Boolean)
    : [];

  return (
    <>
      <PageHeader
        title="Overview"
        description={
          d
            ? `${fmtDay(d.date, { weekday: "long", day: "numeric", month: "long" })} · ${d.headcount.total} people in scope`
            : "Today at a glance"
        }
      />

      {q.isError ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => q.refetch()} feature="The overview" />
        </Card>
      ) : (
        <div className="space-y-6">
          <section aria-label="Headcount" className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
            {!d
              ? Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-[92px] rounded-xl" />)
              : CARD_STATUSES.map((s) => (
                  <StatCard key={s} status={s} value={d.headcount[s] ?? 0} total={d.headcount.total} />
                ))}
          </section>

          <div className="grid gap-6 lg:grid-cols-3">
            <Card as="section" className="flex min-w-0 flex-col lg:col-span-2">
              <CardHeader title="Hours worked" description="Last 14 days, all people in scope" />
              <div className="flex flex-1 flex-col px-2 pb-4 sm:px-3">
                {!chart ? (
                  <Skeleton className="m-2 h-64" />
                ) : chart.every((c) => c.worked === 0 && c.away === 0) ? (
                  <EmptyState title="No hours recorded yet" description="Worked time shows up here after people start checking in." className="py-12" />
                ) : (
                  <div role="img" aria-label="Area chart of worked and away hours for the last 14 days" className="min-h-64 flex-1">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={chart} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                        <defs>
                          <linearGradient id="gWorked" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} />
                            <stop offset="100%" stopColor="var(--accent)" stopOpacity={0.02} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid stroke="var(--line)" vertical={false} />
                        <XAxis
                          dataKey="date"
                          tickLine={false}
                          axisLine={false}
                          tick={{ fill: "var(--muted)", fontSize: 12 }}
                          tickFormatter={(v: string) => fmtDay(v, { day: "numeric", month: "short" })}
                          interval="preserveStartEnd"
                          minTickGap={24}
                        />
                        <YAxis
                          tickLine={false}
                          axisLine={false}
                          tick={{ fill: "var(--muted)", fontSize: 12 }}
                          tickFormatter={(v: number) => `${v}h`}
                          width={48}
                        />
                        <Tooltip content={ChartTooltip} cursor={{ stroke: "var(--line-strong)" }} />
                        <Area
                          type="monotone"
                          dataKey="away"
                          stackId="h"
                          stroke="var(--warn)"
                          strokeWidth={1.5}
                          fill="var(--warn)"
                          fillOpacity={0.12}
                        />
                        <Area
                          type="monotone"
                          dataKey="worked"
                          stackId="h"
                          stroke="var(--accent)"
                          strokeWidth={2}
                          fill="url(#gWorked)"
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
                <div className="flex items-center gap-4 px-3 pt-1 text-xs text-muted">
                  <span className="flex items-center gap-1.5">
                    <span className="size-2 rounded-full bg-accent" /> Worked at desk
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="size-2 rounded-full bg-warn" /> Away from desk
                  </span>
                </div>
              </div>
            </Card>

            <div className="min-w-0 space-y-6">
              <Card as="section">
                <CardHeader title="Needs attention" description="Setup gaps and open items" />
                <div className="space-y-2 px-4 pb-4 sm:px-5">
                  {!d ? (
                    <>
                      <Skeleton className="h-14" />
                      <Skeleton className="h-14" />
                    </>
                  ) : issues.length ? (
                    issues
                  ) : (
                    <div className="flex items-center gap-2 rounded-lg bg-success-soft px-3 py-3 text-sm text-success">
                      <CheckCircle2 className="size-4 shrink-0" /> Everything is set up. Nothing needs attention.
                    </div>
                  )}
                </div>
              </Card>

              <Card as="section">
                <CardHeader title="Late today" description="First check-in after office start" />
                <div className="px-4 pb-4 sm:px-5">
                  {!d ? (
                    <Skeleton className="h-16" />
                  ) : d.late_today.length === 0 ? (
                    <p className="flex items-center gap-2 py-2 text-sm text-muted">
                      <Clock className="size-4" /> No late arrivals so far.
                    </p>
                  ) : (
                    <ul className="divide-y divide-line">
                      {d.late_today.map((p) => (
                        <li key={p.employee_id} className="flex items-center gap-3 py-2">
                          <Avatar name={p.name} size={28} />
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{p.name}</span>
                          <span className="text-sm text-warn tabular-nums">{fmtTime(p.first_check_in)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </Card>
            </div>
          </div>

          {d && d.headcount.outside > 0 && (
            <p className="text-xs text-muted">
              {d.headcount.outside} {d.headcount.outside === 1 ? "person is" : "people are"} currently reporting from
              outside the office. Total worked today across the team:{" "}
              {formatMinutes(d.daily[d.daily.length - 1]?.worked_minutes)}.
            </p>
          )}
        </div>
      )}
    </>
  );
}
