import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, CheckCircle2, ChevronRight, Copy, Loader2, Monitor, Pencil, Plus, Tag, Trash2, Unlink } from "lucide-react";
import { api } from "../api/client";
import type { DeskDevice, PairingCode } from "../api/types";
import { isUuid, parseOptionalInt, shortUuid, timeAgo } from "../lib/format";
import { officeName, useDeskDevices, useOffices, usePeopleNames } from "../lib/queries";
import { useAuth } from "../lib/auth";
import { useToast } from "../lib/toast";
import { Badge, Card, ChipGroup, EmptyState, ErrorState, PageHeader, Table, TableSkeleton, Td, Th } from "../ui/misc";
import type { Tone } from "../ui/misc";
import { Button, IconButton } from "../ui/Button";
import { Field, Input, Select, Switch } from "../ui/form";
import { Dialog } from "../ui/Dialog";
import { PersonPicker } from "../ui/PersonPicker";
import { cx } from "../ui/cx";

type Kind = "tag" | "desk_app";
type Filter = "all" | "unassigned" | "inactive" | "unpaired";

/** Mirrors the desk idle limit used by the attendance policy: idle longer than this does not count as at the desk. */
const IDLE_LIMIT_SECONDS = 600;
/** A desk app heartbeats every 60 s; three missed beats means it is offline. */
const ONLINE_WINDOW_MS = 180_000;
const PAIRING_POLL_MS = 5_000;

interface DeskState {
  hostname?: string | null;
  os?: string | null;
  agent_version?: string | null;
  locked?: boolean | null;
  idle_seconds?: number | null;
  advertising?: boolean | null;
}
const deviceState = (d: DeskDevice): DeskState => d.device as DeskState;
const isOnline = (d: DeskDevice, now: number) => d.paired && !!d.last_seen_at && now - new Date(d.last_seen_at).getTime() < ONLINE_WINDOW_MS;

function Explainer() {
  return (
    <details className="group mb-5 rounded-xl border border-line bg-surface shadow-card">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-xl px-4 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 text-muted transition-transform group-open:rotate-90" aria-hidden />
        What is a desk device?
      </summary>
      <div className="space-y-3 border-t border-line px-4 py-4 text-sm text-muted text-pretty">
        <p>
          A desk device tells Attendify that someone is really at their desk. The employee&apos;s phone listens for a Bluetooth signal from the device
          near their desk. Time spent elsewhere in the building shows as &quot;away&quot;.
        </p>
        <p>
          <span className="font-medium text-fg">We recommend the office computer.</span> Install Attendify Desk on the employee&apos;s Windows 10 or 11
          computer and it becomes the desk device, so there is no hardware to buy or stick on desks. Its Bluetooth identity changes every five minutes
          so it cannot be copied. It can also report whether the computer is locked or idle, if you switch that on in Settings.
        </p>
        <p>
          <span className="font-medium text-fg">Bluetooth tags</span> are the alternative for desks whose computer cannot broadcast Bluetooth. A tag is a
          small battery-powered device, about the size of a coin, that lasts roughly 1 to 3 years. Any iBeacon-compatible tag works. Register each tag
          with the identifiers it broadcasts:
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <span className="font-medium text-fg">UUID</span>: a long ID, often shared by every tag you buy.
          </li>
          <li>
            <span className="font-medium text-fg">Major / Minor</span>: two numbers (0 to 65535) that tell tags apart.
          </li>
        </ul>
        <p>You can read these in the vendor&apos;s configuration app. Assign a device to one person, or leave it shared to cover a whole zone.</p>
      </div>
    </details>
  );
}

/** Status of an Attendify Desk computer, as a short label and tone. */
function deskStatus(d: DeskDevice, now: number): { label: string; tone: Tone } {
  if (!d.paired) return { label: "Not paired", tone: "warn" };
  if (!isOnline(d, now)) return { label: d.last_seen_at ? `Offline (last seen ${timeAgo(d.last_seen_at, now)})` : "Paired, waiting for first contact", tone: "neutral" };
  const s = deviceState(d);
  if (s.locked) return { label: "Online – locked", tone: "info" };
  const idle = s.idle_seconds ?? 0;
  if (idle >= IDLE_LIMIT_SECONDS) return { label: `Online – idle ${Math.floor(idle / 60)}m`, tone: "warn" };
  return { label: "Online – in use", tone: "success" };
}

function KindChoice({ value, onChange, deskEnabled }: { value: Kind; onChange: (k: Kind) => void; deskEnabled: boolean }) {
  const options: { value: Kind; title: string; hint: string; icon: ReactNode; disabled?: boolean }[] = [
    {
      value: "desk_app",
      title: "Office computer (Attendify Desk app)",
      hint: deskEnabled ? "Recommended. No hardware, and its signal cannot be copied." : "Turn on the Desk app module in Settings first.",
      icon: <Monitor />,
      disabled: !deskEnabled,
    },
    { value: "tag", title: "Bluetooth tag", hint: "A small battery device stuck on the desk.", icon: <Tag /> },
  ];
  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className="mb-1.5 text-[13px] font-medium text-fg">Device type</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((o) => {
          const active = o.value === value;
          return (
            <label
              key={o.value}
              className={cx(
                "flex gap-3 rounded-lg border p-3 transition-colors",
                o.disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
                active ? "border-accent bg-accent-soft" : "border-line-strong bg-surface hover:bg-surface-2",
              )}
            >
              <input
                type="radio"
                name="device-kind"
                value={o.value}
                checked={active}
                disabled={o.disabled}
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

export function DeskDevices() {
  const { me } = useAuth();
  const deskEnabled = !!me?.org.modules.desk_app;
  const [params, setParams] = useSearchParams();
  const [office, setOffice] = useState("");
  const f0 = params.get("filter");
  const [filter, setFilter] = useState<Filter>(f0 === "unassigned" || f0 === "inactive" || f0 === "unpaired" ? f0 : "all");
  const [editing, setEditing] = useState<DeskDevice | "new" | null>(null);
  const [deleting, setDeleting] = useState<DeskDevice | null>(null);
  const [pairing, setPairing] = useState<DeskDevice | null>(null);
  const [unpairing, setUnpairing] = useState<DeskDevice | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Keep "Offline (last seen …)" honest between refreshes.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const offices = useOffices();
  // While a computer is being paired, the list is polled so the dialog can show when it comes online.
  const devices = useDeskDevices(pairing ? PAIRING_POLL_MS : false);
  const all = useMemo(() => devices.data ?? [], [devices.data]);
  const owners = usePeopleNames(all.map((d) => d.assigned_user_id));

  const inOffice = all.filter((d) => !office || d.office_id === office);
  const isUnpaired = (d: DeskDevice) => d.kind === "desk_app" && !d.paired;
  const visible = inOffice.filter((d) =>
    filter === "unassigned" ? !d.assigned_user_id : filter === "inactive" ? !d.active : filter === "unpaired" ? isUnpaired(d) : true,
  );
  const deskCount = all.filter((d) => d.kind === "desk_app").length;
  const changeFilter = (f: Filter) => {
    setFilter(f);
    setParams(f === "all" ? {} : { filter: f }, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Desk devices"
        description="Proof that someone is at their desk: an office computer running Attendify Desk, or a Bluetooth tag."
        actions={
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
            Add desk device
          </Button>
        }
      />
      <Explainer />

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <ChipGroup
          label="Filter desk devices"
          value={filter}
          onChange={changeFilter}
          options={[
            { value: "all", label: "All", count: inOffice.length },
            { value: "unassigned", label: "Shared / unassigned", count: inOffice.filter((d) => !d.assigned_user_id).length },
            { value: "inactive", label: "Inactive", count: inOffice.filter((d) => !d.active).length },
            ...(deskCount > 0 || filter === "unpaired"
              ? [{ value: "unpaired" as const, label: "Computers not paired", count: inOffice.filter(isUnpaired).length }]
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
        {devices.isLoading ? (
          <TableSkeleton rows={5} />
        ) : devices.isError ? (
          <ErrorState error={devices.error} onRetry={() => devices.refetch()} feature="Desk devices" />
        ) : visible.length === 0 ? (
          <EmptyState
            icon={<Monitor />}
            title={all.length ? "No desk devices match this filter" : "No desk devices yet"}
            description={
              all.length
                ? undefined
                : "Install Attendify Desk on each office computer and pair it here, or stick a Bluetooth tag on each desk and register it."
            }
            action={
              !all.length ? (
                <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
                  Add desk device
                </Button>
              ) : undefined
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Device</Th>
                <Th>Type</Th>
                <Th>Status</Th>
                <Th className="max-md:hidden">Identifiers</Th>
                <Th>Assigned to</Th>
                <Th className="max-lg:hidden">Office</Th>
                <Th align="right">
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {visible.map((d) => {
                const isDesk = d.kind === "desk_app";
                const state = isDesk ? deskStatus(d, now) : null;
                const host = deviceState(d).hostname;
                return (
                  <tr key={d.id} className="hover:bg-surface-2/50">
                    <Td>
                      <p className="flex items-center gap-2 font-medium">
                        {d.name}
                        {!d.active && <Badge>Inactive</Badge>}
                      </p>
                      <p className="font-mono text-xs whitespace-nowrap text-muted md:hidden">
                        {isDesk ? (host ?? "Not paired") : d.ibeacon_uuid ? shortUuid(d.ibeacon_uuid) : ""}
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
                          {host && <p className="mt-1 font-mono text-xs whitespace-nowrap text-muted">{host}</p>}
                        </>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </Td>
                    <Td className="max-md:hidden">
                      {isDesk ? (
                        <span className="text-xs text-muted">Rotates every 5 minutes</span>
                      ) : (
                        <>
                          <p className="font-mono text-xs" title={d.ibeacon_uuid ?? undefined}>
                            {d.ibeacon_uuid ? shortUuid(d.ibeacon_uuid) : "—"}
                          </p>
                          <p className="text-xs text-muted tabular-nums">
                            Major {d.major ?? "—"} · Minor {d.minor ?? "—"}
                          </p>
                        </>
                      )}
                    </Td>
                    <Td>
                      {d.assigned_user_id ? <span>{owners.get(d.assigned_user_id) ?? "Someone"}</span> : <Badge tone="info">Shared</Badge>}
                    </Td>
                    <Td className="text-muted max-lg:hidden">{officeName(offices.data, d.office_id)}</Td>
                    <Td align="right">
                      <div className="flex justify-end gap-0.5">
                        {isDesk && (
                          <>
                            <Button size="sm" variant="secondary" onClick={() => setPairing(d)}>
                              {d.paired ? "Re-pair" : "Pair a computer"}
                            </Button>
                            {d.paired && (
                              <IconButton label={`Unpair ${d.name}`} onClick={() => setUnpairing(d)}>
                                <Unlink className="size-4" />
                              </IconButton>
                            )}
                          </>
                        )}
                        <IconButton label={`Edit ${d.name}`} onClick={() => setEditing(d)}>
                          <Pencil className="size-4" />
                        </IconButton>
                        <IconButton label={`Delete ${d.name}`} onClick={() => setDeleting(d)}>
                          <Trash2 className="size-4" />
                        </IconButton>
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>

      {editing && (
        <DeviceDialog
          device={editing === "new" ? null : editing}
          defaultOffice={office || offices.data?.[0]?.id || ""}
          deskEnabled={deskEnabled}
          onClose={() => setEditing(null)}
          onSaved={(saved, created) => {
            setEditing(null);
            // A new office computer is useless until it is paired, so go straight to the code.
            if (created && saved.kind === "desk_app") setPairing(saved);
          }}
        />
      )}
      {deleting && <DeleteDialog device={deleting} onClose={() => setDeleting(null)} />}
      {pairing && <PairDialog device={pairing} live={all.find((d) => d.id === pairing.id)} onClose={() => setPairing(null)} />}
      {unpairing && <UnpairDialog device={unpairing} onClose={() => setUnpairing(null)} />}
    </>
  );
}

function DeviceDialog({
  device,
  defaultOffice,
  deskEnabled,
  onClose,
  onSaved,
}: {
  device: DeskDevice | null;
  defaultOffice: string;
  deskEnabled: boolean;
  onClose: () => void;
  onSaved: (saved: DeskDevice, created: boolean) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const offices = useOffices();
  const [kind, setKind] = useState<Kind>((device?.kind as Kind | undefined) ?? (deskEnabled ? "desk_app" : "tag"));
  const [f, setF] = useState({
    name: device?.name ?? "",
    office_id: device?.office_id ?? defaultOffice,
    uuid: device?.kind === "tag" ? (device.ibeacon_uuid ?? "") : "",
    major: device?.major?.toString() ?? "",
    minor: device?.minor?.toString() ?? "",
    active: device?.active ?? true,
  });
  const [assigned, setAssigned] = useState<string | null>(device?.assigned_user_id ?? null);
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));
  const isTag = kind === "tag";

  const major = parseOptionalInt(f.major);
  const minor = parseOptionalInt(f.minor);
  const uuidErr = isTag && f.uuid && !isUuid(f.uuid) ? "Enter a UUID like 2f234454-cf6d-4a0f-adf2-f4911ba9ffa6" : null;

  const m = useMutation({
    mutationFn: () => {
      const common = { name: f.name.trim(), office_id: f.office_id || null, assigned_user_id: assigned };
      if (device) {
        // The kind cannot change, and a desk app's identifiers belong to the server.
        const body = isTag
          ? { ...common, active: f.active, ibeacon_uuid: f.uuid.trim().toLowerCase(), major, minor }
          : { ...common, active: f.active };
        return api<DeskDevice>(`/places/desk-devices/${device.id}`, { method: "PATCH", body });
      }
      const body = isTag ? { ...common, kind, ibeacon_uuid: f.uuid.trim().toLowerCase(), major, minor } : { ...common, kind };
      return api<DeskDevice>("/places/desk-devices", { method: "POST", body });
    },
    onSuccess: (saved) => {
      toast.success(device ? "Desk device saved" : "Desk device added");
      qc.invalidateQueries({ queryKey: ["desk-devices"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onSaved(saved, !device);
    },
    onError: (e: Error) => setError(e.message),
  });

  const invalid =
    !f.name.trim() || (isTag && (!isUuid(f.uuid) || major == null || minor == null));

  return (
    <Dialog
      open
      onClose={onClose}
      title={device ? `Edit ${device.name}` : "Add desk device"}
      description={isTag ? "Copy the identifiers from the tag vendor's configuration app." : "Attendify Desk creates its own identity when you pair the computer next."}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" form="device-form" variant="primary" loading={m.isPending}>
            {device ? "Save changes" : isTag ? "Add tag" : "Add and pair"}
          </Button>
        </>
      }
    >
      <form
        id="device-form"
        noValidate
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          setError(null);
          if (!invalid) m.mutate();
        }}
      >
        {error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
            {error}
          </p>
        )}
        {!device && <KindChoice value={kind} onChange={setKind} deskEnabled={deskEnabled} />}
        <Field label="Name" hint='For example "Desk 14" or "Floor 2 east".' error={touched && !f.name.trim() ? "Give the device a name" : null}>
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
              <Field label="Major" hint="0 to 65535" error={major === undefined || (touched && major === null) ? "Whole number up to 65535" : null}>
                {(p) => <Input {...p} inputMode="numeric" value={f.major} onChange={(e) => set("major", e.target.value)} />}
              </Field>
              <Field label="Minor" hint="0 to 65535" error={minor === undefined || (touched && minor === null) ? "Whole number up to 65535" : null}>
                {(p) => <Input {...p} inputMode="numeric" value={f.minor} onChange={(e) => set("minor", e.target.value)} />}
              </Field>
            </div>
          </>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Office">
            {(p) => (
              <Select {...p} value={f.office_id} onChange={(e) => set("office_id", e.target.value)}>
                <option value="">No office</option>
                {offices.data?.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Assigned to" hint="Leave empty to share it with the whole office.">
            {(p) => <PersonPicker id={p.id} value={assigned} onChange={setAssigned} emptyLabel="Shared (whole office)" />}
          </Field>
        </div>
        {device && <Switch checked={f.active} onChange={(v) => set("active", v)} label="Active" hint="Inactive devices are ignored when checking in." />}
      </form>
    </Dialog>
  );
}

function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function PairDialog({ device, live, onClose }: { device: DeskDevice; live: DeskDevice | undefined; onClose: () => void }) {
  const toast = useToast();
  const current = live ?? device;
  // Re-pairing: the computer may already be online, so only a *new* pairing counts as success.
  const [pairedBefore] = useState(() => device.paired_at ?? null);
  const [pairing, setPairing] = useState<PairingCode | null>(null);
  const [copied, setCopied] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const gen = useMutation({
    mutationFn: () => api<PairingCode>(`/places/desk-devices/${device.id}/pairing-code`, { method: "POST" }),
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

  const success = current.paired && (current.paired_at ?? null) !== pairedBefore && isOnline(current, now);
  const linked = current.paired && (current.paired_at ?? null) !== pairedBefore && !success;

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
      description={success ? undefined : `Attendify Desk on the computer for ${device.name}.`}
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
            <span className="font-medium">{deviceState(current).hostname ?? "The computer"}</span> is online and counts as {device.name}.
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
                <p className="font-medium text-warn">Expired. Generate a new code.</p>
                <Button variant="primary" loading={gen.isPending} onClick={() => mutate()}>
                  New code
                </Button>
              </div>
            ) : (
              <>
                <p aria-label={`Pairing code ${pairing.code}`} className="font-mono text-4xl font-semibold tracking-[0.1em] tabular-nums select-all sm:text-5xl">
                  {pairing.code}
                </p>
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
            <li>Open it and enter this code. It works once.</li>
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

function UnpairDialog({ device, onClose }: { device: DeskDevice; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: () => api<DeskDevice>(`/places/desk-devices/${device.id}/unpair`, { method: "POST" }),
    onSuccess: () => {
      toast.success(`Unpaired ${device.name}`);
      qc.invalidateQueries({ queryKey: ["desk-devices"] });
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
        {device.name} will stop counting as a desk signal until a computer is paired again. The device token and secret on that computer stop working, and
        the Attendify Desk app will show that it was unpaired.
      </p>
    </Dialog>
  );
}

function DeleteDialog({ device, onClose }: { device: DeskDevice; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: () => api(`/places/desk-devices/${device.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success(`Deleted ${device.name}`);
      qc.invalidateQueries({ queryKey: ["desk-devices"] });
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
      title="Delete desk device?"
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
        {device.name} will stop counting as a desk signal. If someone depends on it, they will not be able to check in with proximity until another device is set up.
        To pause it instead, edit it and switch Active off.
      </p>
    </Dialog>
  );
}
