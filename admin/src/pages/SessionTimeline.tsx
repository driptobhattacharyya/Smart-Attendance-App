import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { SessionDetail, SessionState } from "../lib/types";
import { fmtTime, formatMinutes, SESSION_STATE_LABEL, signalLevel } from "../lib/format";
import { Badge, ErrorState, SignalBars, Skeleton } from "../ui/misc";
import type { Tone } from "../ui/misc";

const GRACE_MS = 15 * 60_000;
type SegState = SessionState | "silent";

const COLOR: Record<SegState, string> = {
  present: "var(--success)",
  away: "var(--warn)",
  outside: "var(--danger)",
  silent: "var(--line-strong)",
};
const TONE: Record<SessionState, Tone> = { present: "success", away: "warn", outside: "danger" };

interface Segment {
  from: number;
  to: number;
  state: SegState;
}

function buildSegments(d: SessionDetail): { segments: Segment[]; start: number; end: number } {
  const start = new Date(d.started_at).getTime();
  const log = [...d.presence_log].sort((a, b) => +new Date(a.at) - +new Date(b.at));
  const lastLog = log.length ? new Date(log[log.length - 1].at).getTime() : start;
  const end = Math.max(d.ended_at ? new Date(d.ended_at).getTime() : lastLog, start + 60_000);

  if (!log.length) {
    return { segments: [{ from: start, to: end, state: d.state ?? "present" }], start, end };
  }
  const segments: Segment[] = [];
  const push = (from: number, to: number, state: SegState) => {
    if (to > from) segments.push({ from, to: Math.min(to, end), state });
  };
  const first = new Date(log[0].at).getTime();
  push(start, first, "present"); // checking in requires being at the desk
  log.forEach((e, i) => {
    const at = new Date(e.at).getTime();
    const nextAt = i + 1 < log.length ? new Date(log[i + 1].at).getTime() : end;
    const credited = Math.min(nextAt, at + GRACE_MS);
    push(at, credited, e.state);
    push(credited, nextAt, "silent");
  });
  return { segments, start, end };
}

function hourTicks(start: number, end: number): number[] {
  const span = end - start;
  const step = span > 6 * 3600_000 ? 2 * 3600_000 : 3600_000;
  const out: number[] = [];
  for (let t = Math.ceil(start / step) * step; t < end; t += step) out.push(t);
  // Too crowded on very short sessions
  return span < 30 * 60_000 ? [] : out;
}

export function SessionTimeline({ sessionId }: { sessionId: string }) {
  const q = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => api<SessionDetail>(`/manager/sessions/${sessionId}`),
  });

  const built = useMemo(() => (q.data ? buildSegments(q.data) : null), [q.data]);

  if (q.isLoading) return <Skeleton className="h-28 w-full" />;
  if (q.isError || !q.data || !built) {
    return <ErrorState error={q.error} onRetry={() => q.refetch()} feature="The session timeline" />;
  }
  const d = q.data;
  const { segments, start, end } = built;
  const span = end - start;
  const ticks = hourTicks(start, end);
  const used = new Set(segments.map((s) => s.state));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-2 text-center">
        <Stat label="At desk" value={formatMinutes(d.present_minutes || d.minutes, "0m")} tone="success" />
        <Stat label="Away" value={formatMinutes(d.away_minutes, "0m")} tone="warn" />
        <Stat label="Counted" value={formatMinutes(d.minutes, "0m")} tone="neutral" />
      </div>

      <div>
        <div
          className="relative h-9 overflow-hidden rounded-lg bg-surface-2"
          role="img"
          aria-label={`Timeline from ${fmtTime(d.started_at)} to ${fmtTime(new Date(end).toISOString())}`}
        >
          {segments.map((s, i) => (
            <div
              key={i}
              title={`${SESSION_STATE_LABEL[s.state]}: ${fmtTime(new Date(s.from).toISOString())} – ${fmtTime(new Date(s.to).toISOString())}`}
              className="absolute inset-y-0"
              style={{
                left: `${((s.from - start) / span) * 100}%`,
                width: `${Math.max(((s.to - s.from) / span) * 100, 0.3)}%`,
                background: COLOR[s.state],
                opacity: s.state === "silent" ? 0.55 : 1,
              }}
            />
          ))}
        </div>
        <div className="relative mt-1 h-4 text-[11px] text-muted tabular-nums">
          <span className="absolute left-0">{fmtTime(d.started_at)}</span>
          {ticks.map((t) => {
            const pos = ((t - start) / span) * 100;
            if (pos < 12 || pos > 88) return null;
            return (
              <span key={t} className="absolute -translate-x-1/2" style={{ left: `${pos}%` }}>
                {fmtTime(new Date(t).toISOString())}
              </span>
            );
          })}
          <span className="absolute right-0">{d.ended_at ? fmtTime(d.ended_at) : "now"}</span>
        </div>
        <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
          {(["present", "away", "outside", "silent"] as SegState[])
            .filter((s) => used.has(s))
            .map((s) => (
              <li key={s} className="flex items-center gap-1.5">
                <span className="size-2.5 rounded-sm" style={{ background: COLOR[s], opacity: s === "silent" ? 0.55 : 1 }} />
                {SESSION_STATE_LABEL[s]}
                {s === "silent" && " (phone not reporting)"}
              </li>
            ))}
        </ul>
      </div>

      {d.check_in && (
        <p className="text-xs text-muted">
          Checked in {d.check_in.distance_m != null ? `${Math.round(d.check_in.distance_m)} m from the office centre` : ""}
          {d.check_in.best_rssi != null ? `, desk signal ${d.check_in.best_rssi} dBm` : ""}.
        </p>
      )}

      {d.presence_log.length > 0 ? (
        <div>
          <h4 className="mb-2 text-xs font-medium tracking-wide text-muted uppercase">Presence log</h4>
          <div className="scroll-thin max-h-72 overflow-auto rounded-lg border border-line">
            <table className="w-full text-left text-[13px]">
              <thead className="sticky top-0 bg-surface-2 text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Time</th>
                  <th className="px-3 py-2 font-medium">State</th>
                  <th className="px-3 py-2 text-right font-medium">Distance</th>
                  <th className="px-3 py-2 font-medium">Signal</th>
                </tr>
              </thead>
              <tbody>
                {d.presence_log.map((e, i) => (
                  <tr key={i} className="border-t border-line">
                    <td className="px-3 py-1.5 tabular-nums">{fmtTime(e.at)}</td>
                    <td className="px-3 py-1.5">
                      <Badge tone={TONE[e.state]} dot>
                        {SESSION_STATE_LABEL[e.state]}
                      </Badge>
                    </td>
                    <td className="px-3 py-1.5 text-right text-muted tabular-nums">
                      {e.distance_m != null ? `${Math.round(e.distance_m)} m` : "—"}
                    </td>
                    <td className="px-3 py-1.5">
                      <SignalBars level={signalLevel(e.best_rssi)} rssi={e.best_rssi} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted">
          {d.source === "request"
            ? "This time was added by an approved correction request, so there is no presence log."
            : "No presence log is stored for this session (it may be older than the retention period)."}
        </p>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone: "success" | "warn" | "neutral" }) {
  const color = tone === "success" ? "text-success" : tone === "warn" ? "text-warn" : "text-fg";
  return (
    <div className="rounded-lg border border-line px-2 py-2">
      <p className={`text-base font-semibold tabular-nums ${color}`}>{value}</p>
      <p className="text-xs text-muted">{label}</p>
    </div>
  );
}
