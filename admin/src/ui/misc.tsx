import type { ReactNode } from "react";
import { AlertTriangle, PlugZap, RefreshCw } from "lucide-react";
import { cx } from "./cx";
import { Button } from "./Button";
import { ApiError } from "../api/client";
import { initials, STATUS_HELP, STATUS_LABEL } from "../lib/format";
import type { PresenceStatus } from "../api/attendance";

/* ---------- Badge ---------- */

export type Tone = "neutral" | "success" | "warn" | "danger" | "info" | "violet" | "accent";
const TONE: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted",
  success: "bg-success-soft text-success",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  info: "bg-info-soft text-info",
  violet: "bg-violet-soft text-violet",
  accent: "bg-accent-soft text-accent-text",
};

export function Badge({
  tone = "neutral",
  dot,
  children,
  title,
  className,
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        TONE[tone],
        className,
      )}
    >
      {dot && <span className="size-1.5 rounded-full bg-current" aria-hidden />}
      {children}
    </span>
  );
}

export const STATUS_TONE: Record<PresenceStatus, Tone> = {
  present: "success",
  away: "warn",
  stale: "neutral",
  outside: "danger",
  not_checked_in: "neutral",
  on_leave: "violet",
};

export function StatusBadge({ status }: { status: PresenceStatus }) {
  return (
    <Badge tone={STATUS_TONE[status]} dot title={STATUS_HELP[status]}>
      {STATUS_LABEL[status]}
    </Badge>
  );
}

/* ---------- Layout bits ---------- */

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
      <div className="min-w-0 flex-1 basis-72">
        <h1 className="text-xl font-semibold tracking-tight text-balance">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted text-pretty">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({
  children,
  className,
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  as?: "div" | "section";
}) {
  return (
    <Tag className={cx("rounded-xl border border-line bg-surface shadow-card", className)}>{children}</Tag>
  );
}

export function CardHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 px-4 pt-4 pb-3 sm:px-5">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold">{title}</h2>
        {description && <p className="text-xs text-muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton", className)} aria-hidden />;
}

export function Avatar({ name, size = 32 }: { name: string; size?: number }) {
  // Stable hue per name so people are recognisable at a glance.
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return (
    <span
      aria-hidden
      className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold select-none"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.38,
        background: `hsl(${h} 70% 50% / 0.16)`,
        color: `hsl(${h} 65% var(--avatar-l))`,
        boxShadow: `inset 0 0 0 1px hsl(${h} 70% 50% / 0.25)`,
      }}
    >
      {initials(name)}
    </span>
  );
}

export function SignalBars({ level, rssi }: { level: number; rssi: number | null }) {
  const label = rssi == null ? "No signal reading" : `Signal ${rssi} dBm`;
  return (
    <span className="inline-flex items-center gap-2" title={label}>
      <span className="flex h-4 items-end gap-0.5" role="img" aria-label={label}>
        {[1, 2, 3, 4].map((i) => (
          <span
            key={i}
            style={{ height: `${25 * i}%` }}
            className={cx("w-1 rounded-sm", i <= level ? "bg-success" : "bg-line-strong")}
          />
        ))}
      </span>
      <span className="text-xs text-muted tabular-nums">{rssi == null ? "" : rssi}</span>
    </span>
  );
}

/* ---------- States ---------- */

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("flex flex-col items-center px-6 py-14 text-center", className)}>
      {icon && (
        <div className="mb-3 flex size-11 items-center justify-center rounded-xl bg-surface-2 text-muted [&_svg]:size-5">
          {icon}
        </div>
      )}
      <h3 className="text-sm font-semibold">{title}</h3>
      {description && <p className="mt-1 max-w-sm text-sm text-muted text-pretty">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  feature,
}: {
  error: unknown;
  onRetry?: () => void;
  feature?: string;
}) {
  if (error instanceof ApiError && error.unavailable) {
    return (
      <EmptyState
        icon={<PlugZap />}
        title={`${feature ?? "This view"} is not available yet`}
        description="The server you are connected to has not been updated with this feature. It will appear automatically once the backend is upgraded."
        action={onRetry && <Button onClick={onRetry} icon={<RefreshCw className="size-4" />}>Check again</Button>}
      />
    );
  }
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <EmptyState
      icon={<AlertTriangle className="text-danger" />}
      title="Could not load this"
      description={message}
      action={onRetry && <Button onClick={onRetry} icon={<RefreshCw className="size-4" />}>Try again</Button>}
    />
  );
}

/* ---------- Chips / Tabs ---------- */

export function ChipGroup<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string; count?: number }[];
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            className={cx(
              "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[13px] font-medium transition-colors",
              active
                ? "border-accent bg-accent-soft text-accent-text"
                : "border-line-strong bg-surface text-muted hover:bg-surface-2 hover:text-fg",
            )}
          >
            {o.label}
            {o.count !== undefined && (
              <span className={cx("text-xs tabular-nums", active ? "text-accent-text" : "text-subtle")}>
                {o.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function Tabs<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string; count?: number }[];
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="scroll-thin flex gap-1 overflow-x-auto border-b border-line">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(o.value)}
            className={cx(
              "-mb-px inline-flex h-10 items-center gap-2 border-b-2 px-3 text-sm font-medium whitespace-nowrap transition-colors",
              active ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg",
            )}
          >
            {o.label}
            {o.count !== undefined && (
              <span
                className={cx(
                  "rounded-full px-1.5 text-xs tabular-nums",
                  active ? "bg-accent-soft text-accent-text" : "bg-surface-2 text-muted",
                )}
              >
                {o.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/* ---------- Table ---------- */

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("scroll-thin relative overflow-x-auto", className)}>
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  );
}

export function Th({
  children,
  className,
  align,
}: {
  children?: ReactNode;
  className?: string;
  align?: "right";
}) {
  return (
    <th
      scope="col"
      className={cx(
        "border-b border-line bg-surface-2/60 px-4 py-2.5 text-xs font-medium tracking-wide text-muted uppercase",
        align === "right" && "text-right",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className,
  align,
}: {
  children?: ReactNode;
  className?: string;
  align?: "right";
}) {
  return (
    <td className={cx("border-b border-line px-4 py-3 align-middle", align === "right" && "text-right", className)}>
      {children}
    </td>
  );
}

export function TableSkeleton({ rows = 6, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="divide-y divide-line" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex items-center gap-4 px-4 py-3.5">
          <Skeleton className="size-8 shrink-0 rounded-full" />
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={c} className={cx("h-4", c === 0 ? "w-40" : "w-24 max-sm:hidden")} />
          ))}
        </div>
      ))}
    </div>
  );
}
