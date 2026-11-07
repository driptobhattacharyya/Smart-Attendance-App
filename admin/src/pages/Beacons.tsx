import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bluetooth, Check, CheckCircle2, ChevronRight, Copy, Loader2, Monitor, Pencil, Plus, Tag, Trash2, Unlink } from "lucide-react";
import { api } from "../lib/api";
import type { Beacon, BeaconKind, PairingCode, User } from "../lib/types";
import { isUuid, parseOptionalInt, shortUuid, timeAgo } from "../lib/format";
import { officeName, useBeacons, useOffices, usePeople } from "../lib/queries";
import { useAuth } from "../lib/auth";
import { useToast } from "../lib/toast";
import { Badge, Card, ChipGroup, EmptyState, ErrorState, PageHeader, Table, TableSkeleton, Td, Th } from "../ui/misc";
import type { Tone } from "../ui/misc";
import { Button, IconButton } from "../ui/Button";
import { Field, Input, Select, Switch } from "../ui/form";
import { Dialog } from "../ui/Dialog";
import { cx } from "../ui/cx";

type Filter = "all" | "unassigned" | "inactive" | "unpaired";

/** Mirrors DESK_IDLE_LIMIT_SECONDS on the server: idle longer than this does not count as at the desk. */
const IDLE_LIMIT_SECONDS = 600;
const PAIRING_POLL_MS = 5_000;

function Explainer() {
  return (
    <details className="group mb-5 rounded-xl border border-line bg-surface shadow-card">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-xl px-4 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 text-muted transition-transform group-open:rotate-90" aria-hidden />
        What is a beacon?
      </summary>
      <div className="space-y-3 border-t border-line px-4 py-4 text-sm text-muted text-pretty">
        <p>
          A beacon tells Attendify that someone is really at their desk. The employee&apos;s phone listens for a Bluetooth signal from the beacon near
          their desk. Time spent elsewhere in the building shows as &quot;away&quot;.
        </p>
        <p>
          <span className="font-medium text-fg">We recommend the office computer.</span> Install Attendify Desk on the employee&apos;s Windows 10 or 11
          computer and it becomes the beacon, so there is no hardware to buy or stick on desks. It also reports whether the computer is locked or idle,
          so a locked screen or 10 minutes without typing or mouse movement counts as away. Desktops without built-in Bluetooth can use a cheap USB
          Bluetooth adapter.
        </p>
        <p>
          <span className="font-medium text-fg">Bluetooth tags</span> are the alternative for desks whose computer cannot broadcast Bluetooth. A tag is a
          small battery-powered device, about the size of a coin, that lasts roughly 1 to 3 years. Any iBeacon-compatible tag works, from any vendor.
          Register each tag with the identifiers it broadcasts:
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <span className="font-medium text-fg">UUID</span>: a long ID, often shared by every tag you buy.
          </li>
          <li>
            <span className="font-medium text-fg">Major / Minor</span>: two numbers (0 to 65535) that tell tags apart. Leave blank to match any.
          </li>
        </ul>
        <p>You can read these in the vendor&apos;s configuration app after pairing the tag with your phone. Assign a beacon to one person, or leave it shared to cover a whole zone.</p>
      </div>
    </details>
  );
}

/** Status of an Attendify Desk computer, as a short label and tone. */
function deskState(b: Beacon, now: number): { label: string; tone: Tone } {
  if (!b.paired) return { label: "Not paired", tone: "warn" };
  if (!b.online) return { label: `Offline (last seen ${timeAgo(b.device?.last_seen_at, now)})`, tone: "neutral" };
  if (b.device?.locked) return { label: "Online – locked", tone: "info" };
  const idle = b.device?.idle_seconds ?? 0;
  if (idle >= IDLE_LIMIT_SECONDS) return { label: `Online – idle ${Math.floor(idle / 60)}m`, tone: "warn" };
  return { label: "Online – in use", tone: "success" };
}

function KindChoice({ value, onChange }: { value: BeaconKind; onChange: (k: BeaconKind) => void }) {
  const options: { value: BeaconKind; title: string; hint: string; icon: ReactNode }[] = [
    {
      value: "desk_app",
      title: "Office computer (Attendify Desk app)",
      hint: "Recommended. No hardware, and it reports locked or idle.",
      icon: <Monitor />,
    },
    { value: "tag", title: "Bluetooth tag", hint: "A small battery device stuck on the desk.", icon: <Tag /> },
  ];
  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className="mb-1.5 text-[13px] font-medium text-fg">Beacon type</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((o) => {
          const active = o.value === value;
          return (
            <label
              key={o.value}
              className={cx(
                "flex cursor-pointer gap-3 rounded-lg border p-3 transition-colors",
                active ? "border-accent bg-accent-soft" : "border-line-strong bg-surface hover:bg-surface-2",
              )}
            >
              <input
                type="radio"
                name="beacon-kind"
                value={o.value}
                checked={active}
                onChange={() => onChange(o.value)}
                className="mt-1 accent-[var(--accent)]"
              />
              <span className="min-w-0">
                <span className="flex items-center gap-2 text-sm font-medium">
                  <span className="text-muted [&_svg]:size-4" aria-hidden>
                    {o.icon}
                  </span>
                  {o.title}
                </span>
                <span className="mt-0.5 block text-xs text-muted text-pretty">{o.hint}</span>
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

export function Beacons() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [params, setParams] = useSearchParams();
  const [office, setOffice] = useState("");
  const f0 = params.get("filter");
  const [filter, setFilter] = useState<Filter>(
    f0 === "unassigned" || f0 === "inactive" || f0 === "unpaired" ? f0 : "all",
  );
  const [editing, setEditing] = useState<Beacon | "new" | null>(null);
  const [deleting, setDeleting] = useState<Beacon | null>(null);
  const [pairing, setPairing] = useState<Beacon | null>(null);
  const [unpairing, setUnpairing] = useState<Beacon | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Keep "Offline (last seen …)" honest between refreshes.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const offices = useOffices();
  const people = usePeople();
  // While a computer is being paired, the list is polled so the dialog can show when it comes online.
  const beacons = useBeacons(office, pairing ? PAIRING_POLL_MS : false);
  const byId = useMemo(() => new Map((people.data ?? []).map((p) => [p.employee_id, p])), [people.data]);

  const all = beacons.data ?? [];
  const isUnpaired = (b: Beacon) => b.kind === "desk_app" && !b.paired;
  const visible = all.filter((b) =>
    filter === "unassigned" ? !b.assigned_employee_id : filter === "inactive" ? !b.active : filter === "unpaired" ? isUnpaired(b) : true,
  );
  const deskCount = all.filter((b) => b.kind === "desk_app").length;
  const changeFilter = (f: Filter) => {
    setFilter(f);
    setParams(f === "all" ? {} : { filter: f }, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Beacons"
        description="Proof that someone is at their desk: an office computer running Attendify Desk, or a Bluetooth tag."
        actions={
          admin && (
            <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
              Add beacon
            </Button>
          )
        }
      />
      <Explainer />

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <ChipGroup
          label="Filter beacons"
          value={filter}
          onChange={changeFilter}
          options={[
            { value: "all", label: "All", count: all.length },
            { value: "unassigned", label: "Shared / unassigned", count: all.filter((b) => !b.assigned_employee_id).length },
            { value: "inactive", label: "Inactive", count: all.filter((b) => !b.active).length },
            ...(deskCount > 0 || filter === "unpaired"
              ? [{ value: "unpaired" as const, label: "Computers not paired", count: all.filter(isUnpaired).length }]
              : []),
          ]}
        />
        {(offices.data?.length ?? 0) > 1 && (
          <Select aria-label="Office" value={office} onChange={(e) => setOffice(e.target.value)} className="sm:w-48">
            <option value="">All offices</option>
            {offices.data?.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </Select>
        )}
      </div>

      <Card>
        {beacons.isLoading ? (
          <TableSkeleton rows={5} />
        ) : beacons.isError ? (
          <ErrorState error={beacons.error} onRetry={() => beacons.refetch()} feature="Beacons" />
        ) : visible.length === 0 ? (
          <EmptyState
            icon={<Bluetooth />}
            title={all.length ? "No beacons match this filter" : "No beacons registered yet"}
            description={
              all.length
                ? undefined
                : "Install Attendify Desk on each office computer and pair it here, or stick a Bluetooth tag on each desk and register it."
            }
            action={
              !all.length && admin ? (
                <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
                  Add beacon
                </Button>
              ) : undefined
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Beacon</Th>
                <Th>Type</Th>
                <Th>Status</Th>
                <Th className="max-md:hidden">Identifiers</Th>
                <Th>Assigned to</Th>
                <Th className="max-lg:hidden">Office</Th>
                {admin && (
                  <Th align="right">
                    <span className="sr-only">Actions</span>
                  </Th>
                )}
              </tr>
            </thead>
            <tbody>
              {visible.map((b) => {
                const owner = b.assigned_employee_id ? byId.get(b.assigned_employee_id) : null;
                const isDesk = b.kind === "desk_app";
                const state = isDesk ? deskState(b, now) : null;
                return (
                  <tr key={b.id} className="hover:bg-surface-2/50">
                    <Td>
                      <p className="flex items-center gap-2 font-medium">
                        {b.name}
                        {!b.active && <Badge>Inactive</Badge>}
                      </p>
                      <p className="font-mono text-xs text-muted md:hidden">
                        {isDesk ? b.device?.hostname ?? "Not paired" : shortUuid(b.uuid)}
                      </p>
                    </Td>
                    <Td>
                      <span className="inline-flex items-center gap-2 text-sm text-muted">
                        {isDesk ? <Monitor className="size-4 shrink-0" aria-hidden /> : <Tag className="size-4 shrink-0" aria-hidden />}
                        <span className="max-md:sr-only">{isDesk ? "Computer" : "Tag"}</span>
                      </span>
                    </Td>
                    <Td>
                      {state ? (
                        <>
                          <Badge tone={state.tone} dot>
                            {state.label}
                          </Badge>
                          {b.device?.hostname && <p className="mt-1 max-w-48 truncate font-mono text-xs text-muted">{b.device.hostname}</p>}
                        </>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </Td>
                    <Td className="max-md:hidden">
                      {isDesk ? (
                        <span className="text-xs text-muted">Set automatically</span>
                      ) : (
                        <>
                          <p className="font-mono text-xs" title={b.uuid}>
                            {shortUuid(b.uuid)}
                          </p>
                          <p className="text-xs text-muted tabular-nums">
                            Major {b.major ?? "any"} · Minor {b.minor ?? "any"}
                          </p>
                        </>
                      )}
                    </Td>
                    <Td>
                      {b.assigned_employee_id ? (
                        <span>{owner?.name ?? b.assigned_employee_id}</span>
                      ) : (
                        <Badge tone="info">Shared</Badge>
                      )}
                    </Td>
                    <Td className="text-muted max-lg:hidden">{officeName(offices.data, b.office_id)}</Td>
                    {admin && (
                      <Td align="right">
                        <div className="flex justify-end gap-0.5">
                          {isDesk && (
                            <>
                              <Button size="sm" variant="secondary" onClick={() => setPairing(b)}>
                                {b.paired ? "Re-pair" : "Pair a computer"}
                              </Button>
                              {b.paired && (
                                <IconButton label={`Unpair ${b.name}`} onClick={() => setUnpairing(b)}>
                                  <Unlink className="size-4" />
                                </IconButton>
                              )}
                            </>
                          )}
                          <IconButton label={`Edit ${b.name}`} onClick={() => setEditing(b)}>
                            <Pencil className="size-4" />
                          </IconButton>
                          <IconButton label={`Delete ${b.name}`} onClick={() => setDeleting(b)}>
                            <Trash2 className="size-4" />
                          </IconButton>
                        </div>
                      </Td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>

      {editing && (
        <BeaconDialog
          beacon={editing === "new" ? null : editing}
          defaultOffice={office || offices.data?.[0]?.id || ""}
          people={people.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={(saved, created) => {
            setEditing(null);
            // A new office computer is useless until it is paired, so go straight to the code.
            if (created && saved.kind === "desk_app") setPairing(saved);
          }}
        />
      )}
      {deleting && <DeleteDialog beacon={deleting} onClose={() => setDeleting(null)} />}
      {pairing && <PairDialog beacon={pairing} live={all.find((b) => b.id === pairing.id)} onClose={() => setPairing(null)} />}
      {unpairing && <UnpairDialog beacon={unpairing} onClose={() => setUnpairing(null)} />}
    </>
  );
}

function BeaconDialog({
  beacon,
  defaultOffice,
  people,
  onClose,
  onSaved,
}: {
  beacon: Beacon | null;
  defaultOffice: string;
  people: User[];
  onClose: () => void;
  onSaved: (saved: Beacon, created: boolean) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const offices = useOffices();
  const [kind, setKind] = useState<BeaconKind>(beacon?.kind ?? "desk_app");
  const [f, setF] = useState({
    name: beacon?.name ?? "",
    office_id: beacon?.office_id ?? defaultOffice,
    uuid: beacon?.uuid ?? "",
    major: beacon?.major?.toString() ?? "",
    minor: beacon?.minor?.toString() ?? "",
    assigned: beacon?.assigned_employee_id ?? "",
    active: beacon?.active ?? true,
  });
  const [touched, setTouched] = useState(false);
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));
  const isTag = kind === "tag";

  const major = parseOptionalInt(f.major);
  const minor = parseOptionalInt(f.minor);
  const uuidErr = isTag && f.uuid && !isUuid(f.uuid) ? "Enter a UUID like 2f234454-cf6d-4a0f-adf2-f4911ba9ffa6" : null;
  const candidates = people.filter((p) => p.office_id === f.office_id && (p.active || p.employee_id === f.assigned));

  const m = useMutation({
    mutationFn: () => {
      const common = {
        name: f.name.trim(),
        office_id: f.office_id,
        assigned_employee_id: f.assigned || null,
        active: f.active,
      };
      if (beacon) {
        // The beacon's kind cannot change, and a desk app's identifiers belong to the server.
        const body = isTag ? { ...common, uuid: f.uuid.trim().toLowerCase(), major, minor } : common;
        return api<Beacon>(`/beacons/${beacon.id}`, { method: "PATCH", body });
      }
      const body = isTag ? { ...common, kind, uuid: f.uuid.trim().toLowerCase(), major, minor } : { ...common, kind };
      return api<Beacon>("/beacons", { method: "POST", body });
    },
    onSuccess: (saved) => {
      toast.success(beacon ? "Beacon saved" : "Beacon added");
      qc.invalidateQueries({ queryKey: ["beacons"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onSaved(saved, !beacon);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const invalid = !f.name.trim() || !f.office_id || (isTag && (!isUuid(f.uuid) || major === undefined || minor === undefined));

  return (
    <Dialog
      open
      onClose={onClose}
      title={beacon ? `Edit ${beacon.name}` : "Add beacon"}
      description={
        isTag ? "Copy the identifiers from the tag vendor's configuration app." : "Attendify Desk assigns the identifiers. Pair the computer next."
      }
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" form="beacon-form" variant="primary" loading={m.isPending}>
            {beacon ? "Save changes" : isTag ? "Add beacon" : "Add and pair"}
          </Button>
        </>
      }
    >
      <form
        id="beacon-form"
        noValidate
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          if (!invalid) m.mutate();
        }}
      >
        {!beacon && <KindChoice value={kind} onChange={setKind} />}
        <Field label="Name" hint='For example "Desk 14" or "Floor 2 east".' error={touched && !f.name.trim() ? "Give the beacon a name" : null}>
          {(p) => <Input {...p} value={f.name} onChange={(e) => set("name", e.target.value)} maxLength={120} autoFocus />}
        </Field>
        {isTag && (
          <>
            <Field label="UUID" error={uuidErr ?? (touched && !f.uuid ? "UUID is required" : null)}>
              {(p) => (
                <Input
                  {...p}
                  className="font-mono text-[13px]"
                  value={f.uuid}
                  onChange={(e) => set("uuid", e.target.value)}
                  spellCheck={false}
                  placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                />
              )}
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Major" hint="0 to 65535, blank = any" error={major === undefined ? "Whole number up to 65535" : null}>
                {(p) => <Input {...p} inputMode="numeric" value={f.major} onChange={(e) => set("major", e.target.value)} />}
              </Field>
              <Field label="Minor" hint="0 to 65535, blank = any" error={minor === undefined ? "Whole number up to 65535" : null}>
                {(p) => <Input {...p} inputMode="numeric" value={f.minor} onChange={(e) => set("minor", e.target.value)} />}
              </Field>
            </div>
          </>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Office">
            {(p) => (
              <Select {...p} value={f.office_id} onChange={(e) => setF((s) => ({ ...s, office_id: e.target.value, assigned: "" }))}>
                <option value="">Select an office…</option>
                {offices.data?.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Assigned to" hint="Only people in the chosen office.">
            {(p) => (
              <Select {...p} value={f.assigned} onChange={(e) => set("assigned", e.target.value)}>
                <option value="">Shared (whole office)</option>
                {candidates.map((u) => (
                  <option key={u.employee_id} value={u.employee_id}>
                    {u.name} ({u.employee_id})
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <Switch checked={f.active} onChange={(v) => set("active", v)} label="Active" hint="Inactive beacons are ignored when checking in." />
      </form>
    </Dialog>
  );
}

function formatCountdown(ms: number): string {
  const total = Math.ceil(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function PairDialog({ beacon, live, onClose }: { beacon: Beacon; live: Beacon | undefined; onClose: () => void }) {
  const toast = useToast();
  const current = live ?? beacon;
  // Re-pairing: the computer may already be online, so only a *new* pairing counts as success.
  const [pairedBefore] = useState(() => beacon.device?.paired_at ?? null);
  const [pairing, setPairing] = useState<PairingCode | null>(null);
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const gen = useMutation({
    mutationFn: () => api<PairingCode>(`/beacons/${beacon.id}/pairing-code`, { method: "POST" }),
    onSuccess: (c) => {
      setPairing(c);
      setCopied(false);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const { mutate } = gen;
  const started = useRef(false);
  useEffect(() => {
    // Strict-mode safe: one code per open dialog.
    if (!started.current) {
      started.current = true;
      mutate();
    }
  }, [mutate]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const msLeft = pairing ? new Date(pairing.expires_at).getTime() - now : 0;
  const expired = !!pairing && msLeft <= 0;

  const success = current.paired && current.online && (current.device?.paired_at ?? null) !== pairedBefore;
  const linked = current.paired && !success;

  const copy = async () => {
    if (!pairing) return;
    try {
      await navigator.clipboard.writeText(pairing.code);
      setCopied(true);
    } catch {
      toast.error("Could not copy. Select the code and copy it by hand.");
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={success ? "Computer paired" : "Pair a computer"}
      description={success ? undefined : `Attendify Desk on the computer for ${beacon.name}.`}
      footer={
        success ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <Button onClick={onClose}>Close</Button>
        )
      }
    >
      {success ? (
        <div className="flex flex-col items-center gap-3 py-4 text-center" role="status">
          <span className="flex size-12 items-center justify-center rounded-full bg-success-soft text-success">
            <CheckCircle2 className="size-6" aria-hidden />
          </span>
          <p className="text-sm text-pretty">
            <span className="font-medium">{current.device?.hostname ?? "The computer"}</span> is online and counts as {beacon.name}.
          </p>
        </div>
      ) : (
        <div className="space-y-5">
          <div className="rounded-xl border border-line-strong bg-surface-2 px-4 py-5 text-center">
            {!pairing ? (
              <p className="flex items-center justify-center gap-2 py-3 text-sm text-muted">
                <Loader2 className="size-4 animate-spin" aria-hidden /> Creating a code…
              </p>
            ) : expired ? (
              <div className="space-y-3 py-1">
                <p className="font-medium text-warn">Expired — generate a new code</p>
                <Button variant="primary" loading={gen.isPending} onClick={() => mutate()}>
                  New code
                </Button>
              </div>
            ) : (
              <>
                <p className="font-mono text-4xl font-semibold tracking-[0.1em] tabular-nums select-all sm:text-5xl">{pairing.code}</p>
                <div className="mt-3 flex flex-wrap items-center justify-center gap-x-3 gap-y-2 text-sm">
                  <span className="text-muted">
                    Expires in <span className="font-mono font-medium text-fg tabular-nums">{formatCountdown(msLeft)}</span>
                  </span>
                  <Button size="sm" onClick={copy} icon={copied ? <Check className="size-4" /> : <Copy className="size-4" />}>
                    {copied ? "Copied" : "Copy code"}
                  </Button>
                </div>
              </>
            )}
          </div>

          <ol className="list-decimal space-y-2 pl-5 text-sm text-pretty marker:font-medium marker:text-muted">
            <li>
              Download Attendify Desk on that computer.{" "}
              <a href="/download/desk" target="_blank" rel="noopener" className="font-medium text-accent-text underline underline-offset-2">
                Get Attendify Desk
              </a>
            </li>
            <li>Open it and enter this code.</li>
            <li>Done when the status here turns Online.</li>
          </ol>

          <p role="status" aria-live="polite" className="flex items-center gap-2 border-t border-line pt-4 text-sm text-muted">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            {linked ? "Paired. Waiting for the computer to come online…" : "Waiting for the computer to connect…"}
          </p>
        </div>
      )}
    </Dialog>
  );
}

function UnpairDialog({ beacon, onClose }: { beacon: Beacon; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: () => api<Beacon>(`/beacons/${beacon.id}/unpair`, { method: "POST" }),
    onSuccess: () => {
      toast.success(`Unpaired ${beacon.name}`);
      qc.invalidateQueries({ queryKey: ["beacons"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Dialog
      open
      onClose={onClose}
      size="sm"
      title="Unpair this computer?"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" loading={m.isPending} onClick={() => m.mutate()}>
            Unpair
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted text-pretty">
        {beacon.name} will stop counting as a desk signal until a computer is paired again. The Attendify Desk app on that computer will show that it was
        unpaired.
      </p>
    </Dialog>
  );
}

function DeleteDialog({ beacon, onClose }: { beacon: Beacon; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: () => api(`/beacons/${beacon.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success(`Deleted ${beacon.name}`);
      qc.invalidateQueries({ queryKey: ["beacons"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Dialog
      open
      onClose={onClose}
      size="sm"
      title="Delete beacon?"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" loading={m.isPending} onClick={() => m.mutate()}>
            Delete
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted text-pretty">
        {beacon.name} will stop counting as a desk signal. If someone depends on it, they will not be able to check in until another beacon is set up.
      </p>
    </Dialog>
  );
}
