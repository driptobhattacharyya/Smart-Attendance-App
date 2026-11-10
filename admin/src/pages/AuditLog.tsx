import { Fragment, useMemo, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { ChevronRight, History } from "lucide-react";
import { api } from "../api/client";
import type { AuditEntry, Page } from "../api/types";
import { useDebounced, usePeopleNames } from "../lib/queries";
import { Badge, Card, EmptyState, ErrorState, PageHeader, Table, TableSkeleton, Td, Th } from "../ui/misc";
import type { Tone } from "../ui/misc";
import { Button } from "../ui/Button";
import { Field, Input, Select } from "../ui/form";
import { PersonPicker } from "../ui/PersonPicker";
import { cx } from "../ui/cx";

/** Prefix groups end with a dot, which the API treats as "starts with". */
const ACTION_GROUPS: { value: string; label: string }[] = [
  { value: "", label: "All actions" },
  { value: "auth.", label: "Sign-ins and sessions" },
  { value: "people.", label: "People" },
  { value: "department.", label: "Departments" },
  { value: "office.", label: "Offices" },
  { value: "device.", label: "Desk devices" },
  { value: "org.", label: "Company settings" },
  { value: "consent.", label: "Consent" },
];
const ACTIONS = [
  "org.signup", "org.update",
  "auth.login", "auth.logout", "auth.logout_all", "auth.session_revoked", "auth.password_changed",
  "auth.password_reset_requested", "auth.password_reset", "auth.invite_accepted", "auth.refresh_reuse_detected",
  "consent.accept", "consent.withdraw",
  "people.create", "people.update", "people.update_self", "people.disable", "people.enable", "people.invite_resent", "people.sessions_revoked",
  "department.create", "department.update", "department.delete",
  "office.create", "office.update", "office.delete",
  "device.create", "device.update", "device.delete", "device.pairing_code", "device.unpair", "device.paired",
];
const TARGET_TYPES = ["organization", "user", "department", "office", "desk_device", "session"];

function actionTone(action: string): Tone {
  if (/delete|disable|revoked|reuse|unpair|withdraw/.test(action)) return "danger";
  if (/create|signup|enable|accept|paired|invite/.test(action)) return "success";
  if (/update|change|reset/.test(action)) return "info";
  return "neutral";
}

/** Local start/end of a YYYY-MM-DD day as an ISO instant. */
function dayStart(d: string) {
  return d ? new Date(`${d}T00:00:00`).toISOString() : "";
}
function dayEnd(d: string) {
  return d ? new Date(`${d}T23:59:59.999`).toISOString() : "";
}

export function AuditLog() {
  const [actor, setActor] = useState<string | null>(null);
  const [group, setGroup] = useState("");
  const [exact, setExact] = useState("");
  const [targetType, setTargetType] = useState("");
  const [targetId, setTargetId] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const debouncedTarget = useDebounced(targetId.trim());
  const filters = {
    actor_user_id: actor ?? "",
    action: exact || group,
    target_type: targetType,
    target_id: debouncedTarget,
    since: dayStart(since),
    until: dayEnd(until),
  };
  const q = useInfiniteQuery({
    queryKey: ["audit", filters],
    initialPageParam: "",
    queryFn: ({ pageParam }) => api<Page<AuditEntry>>("/audit", { query: { ...filters, limit: 50, cursor: pageParam } }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);
  const names = usePeopleNames(rows.map((r) => r.actor_user_id));
  const filtered = Object.values(filters).some(Boolean);

  const reset = () => {
    setActor(null);
    setGroup("");
    setExact("");
    setTargetType("");
    setTargetId("");
    setSince("");
    setUntil("");
  };

  return (
    <>
      <PageHeader title="Audit log" description="Who changed what, and when. Entries cannot be edited or deleted. Newest first." />

      <Card className="mb-4 p-4">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Field label="Who">{(p) => <PersonPicker id={p.id} value={actor} onChange={setActor} emptyLabel="Anyone" />}</Field>
          <Field label="What happened">
            {(p) => (
              <Select
                {...p}
                value={exact || group}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v.endsWith(".") || v === "") {
                    setGroup(v);
                    setExact("");
                  } else {
                    setExact(v);
                    setGroup("");
                  }
                }}
              >
                {ACTION_GROUPS.map((g) => (
                  <option key={g.value} value={g.value}>
                    {g.label}
                  </option>
                ))}
                <optgroup label="Exact action">
                  {ACTIONS.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </optgroup>
              </Select>
            )}
          </Field>
          <Field label="Target type">
            {(p) => (
              <Select {...p} value={targetType} onChange={(e) => setTargetType(e.target.value)}>
                <option value="">Anything</option>
                {TARGET_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t.replace("_", " ")}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Target ID" hint="The id shown in a row's details.">
            {(p) => <Input {...p} value={targetId} onChange={(e) => setTargetId(e.target.value)} spellCheck={false} className="font-mono text-[13px]" />}
          </Field>
          <Field label="From">{(p) => <Input {...p} type="date" value={since} max={until || undefined} onChange={(e) => setSince(e.target.value)} />}</Field>
          <Field label="To">{(p) => <Input {...p} type="date" value={until} min={since || undefined} onChange={(e) => setUntil(e.target.value)} />}</Field>
          <div className="flex items-end">
            {filtered && <Button onClick={reset}>Clear filters</Button>}
          </div>
        </div>
      </Card>

      <Card>
        {q.isLoading ? (
          <TableSkeleton rows={8} cols={4} />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} feature="The audit log" />
        ) : rows.length === 0 ? (
          <EmptyState icon={<History />} title={filtered ? "No entries match" : "Nothing recorded yet"} description={filtered ? "Try widening the dates or clearing a filter." : undefined} />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>
                    <span className="sr-only">Details</span>
                  </Th>
                  <Th>When</Th>
                  <Th>Who</Th>
                  <Th>Action</Th>
                  <Th className="max-md:hidden">Target</Th>
                  <Th className="max-lg:hidden">IP</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const expanded = open === r.id;
                  const hasDiff = r.before != null || r.after != null;
                  return (
                    <Fragment key={r.id}>
                      <tr className={cx("hover:bg-surface-2/50", expanded && "bg-surface-2/40")}>
                        <Td className="w-10 pr-0">
                          <button
                            type="button"
                            aria-expanded={expanded}
                            aria-label={`${expanded ? "Hide" : "Show"} details of ${r.action}`}
                            onClick={() => setOpen(expanded ? null : r.id)}
                            className="inline-flex size-7 items-center justify-center rounded-md text-muted hover:bg-surface-2 hover:text-fg"
                          >
                            <ChevronRight className={cx("size-4 transition-transform", expanded && "rotate-90")} />
                          </button>
                        </Td>
                        <Td className="whitespace-nowrap tabular-nums">
                          {new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit", second: "2-digit" }).format(new Date(r.at))}
                        </Td>
                        <Td>
                          {r.actor_user_id ? (names.get(r.actor_user_id) ?? "Former user") : <span className="text-muted capitalize">{r.actor_kind}</span>}
                        </Td>
                        <Td>
                          <Badge tone={actionTone(r.action)}>{r.action}</Badge>
                        </Td>
                        <Td className="text-muted max-md:hidden">
                          {r.target_type ? <span className="capitalize">{r.target_type.replace("_", " ")}</span> : "—"}
                        </Td>
                        <Td className="font-mono text-xs text-muted max-lg:hidden">{r.ip ?? "—"}</Td>
                      </tr>
                      {expanded && (
                        <tr className="bg-surface-2/40">
                          <td colSpan={6} className="border-b border-line px-4 py-4 sm:pl-14">
                            <dl className="mb-3 grid gap-x-6 gap-y-1 text-xs text-muted sm:grid-cols-2">
                              <div>
                                <dt className="inline">Target id: </dt>
                                <dd className="inline font-mono break-all text-fg">{r.target_id ?? "—"}</dd>
                              </div>
                              <div>
                                <dt className="inline">Entry id: </dt>
                                <dd className="inline font-mono break-all text-fg">{r.id}</dd>
                              </div>
                            </dl>
                            {hasDiff ? <DiffView before={r.before} after={r.after} /> : <p className="text-sm text-muted">No field changes were recorded for this action.</p>}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </Table>
            {q.hasNextPage && (
              <div className="flex justify-center border-t border-line p-3">
                <Button loading={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
                  Load older entries
                </Button>
              </div>
            )}
          </>
        )}
      </Card>
    </>
  );
}

/* ----------------------------------------------------------------- diff view */

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/** Flatten nested objects to dotted keys so nested changes (modules.face) show as one line each. */
function flatten(v: unknown, prefix = "", out: Record<string, unknown> = {}): Record<string, unknown> {
  if (isObj(v)) {
    for (const [k, val] of Object.entries(v)) {
      if (isObj(val)) flatten(val, prefix ? `${prefix}.${k}` : k, out);
      else out[prefix ? `${prefix}.${k}` : k] = val;
    }
  } else if (v !== undefined) {
    out[prefix || "value"] = v;
  }
  return out;
}

function show(v: unknown): string {
  if (v === undefined) return "";
  if (v === null) return "empty";
  if (typeof v === "string") return v === "" ? "(blank)" : v;
  return JSON.stringify(v);
}

function DiffView({ before, after }: { before: unknown; after: unknown }) {
  const [all, setAll] = useState(false);
  const b = flatten(before);
  const a = flatten(after);
  const keys = [...new Set([...Object.keys(b), ...Object.keys(a)])].sort();
  const same = (k: string) => JSON.stringify(b[k]) === JSON.stringify(a[k]);
  const changed = keys.filter((k) => !same(k));
  const shown = all ? keys : changed;
  const created = before == null;
  const removed = after == null;

  return (
    <div>
      <p className="mb-2 text-xs font-medium text-muted">
        {created ? "Created with these values" : removed ? "Removed. Last known values" : `${changed.length} ${changed.length === 1 ? "field" : "fields"} changed`}
      </p>
      <div className="scroll-thin overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full border-collapse text-left font-mono text-xs">
          <caption className="sr-only">Changes</caption>
          <thead>
            <tr className="text-muted">
              <th scope="col" className="w-1/4 border-b border-line px-3 py-2 font-medium">
                Field
              </th>
              <th scope="col" className="w-[37.5%] border-b border-line px-3 py-2 font-medium">
                Before
              </th>
              <th scope="col" className="w-[37.5%] border-b border-line px-3 py-2 font-medium">
                After
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr>
                <td colSpan={3} className="px-3 py-3 font-sans text-muted">
                  No visible field changed.
                </td>
              </tr>
            )}
            {shown.map((k) => {
              const diff = !same(k);
              return (
                <tr key={k}>
                  <th scope="row" className="border-b border-line px-3 py-1.5 align-top font-medium break-all text-fg">
                    {k}
                  </th>
                  <td className={cx("border-b border-line px-3 py-1.5 align-top break-all", diff && k in b ? "bg-danger-soft text-danger" : "text-muted")}>
                    {k in b ? show(b[k]) : ""}
                  </td>
                  <td className={cx("border-b border-line px-3 py-1.5 align-top break-all", diff && k in a ? "bg-success-soft text-success" : "text-muted")}>
                    {k in a ? show(a[k]) : ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {keys.length > changed.length && (
        <button type="button" onClick={() => setAll((x) => !x)} className="mt-2 text-xs font-medium text-accent-text underline underline-offset-2">
          {all ? "Show only changed fields" : `Show all ${keys.length} fields`}
        </button>
      )}
    </div>
  );
}
