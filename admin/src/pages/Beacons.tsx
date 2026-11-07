import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bluetooth, ChevronRight, Pencil, Plus, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import type { Beacon, User } from "../lib/types";
import { isUuid, parseOptionalInt, shortUuid } from "../lib/format";
import { officeName, useBeacons, useOffices, usePeople } from "../lib/queries";
import { useAuth } from "../lib/auth";
import { useToast } from "../lib/toast";
import { Badge, Card, ChipGroup, EmptyState, ErrorState, PageHeader, Table, TableSkeleton, Td, Th } from "../ui/misc";
import { Button, IconButton } from "../ui/Button";
import { Field, Input, Select, Switch } from "../ui/form";
import { Dialog } from "../ui/Dialog";

type Filter = "all" | "unassigned" | "inactive";

function Explainer() {
  return (
    <details className="group mb-5 rounded-xl border border-line bg-surface shadow-card">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-xl px-4 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 text-muted transition-transform group-open:rotate-90" aria-hidden />
        What is a beacon?
      </summary>
      <div className="space-y-3 border-t border-line px-4 py-4 text-sm text-muted text-pretty">
        <p>
          A beacon is a small battery-powered Bluetooth tag, about the size of a coin or matchbox, that lasts roughly 1 to 3 years on a battery. Place one on
          each desk. The employee&apos;s phone measures the tag&apos;s signal strength to confirm they are really at their desk. Without it, time spent
          elsewhere in the building shows as &quot;away&quot;.
        </p>
        <p>Any iBeacon-compatible tag works, from any vendor. Register each tag here with the identifiers it broadcasts:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>
            <span className="font-medium text-fg">UUID</span>: a long ID, often shared by every tag you buy.
          </li>
          <li>
            <span className="font-medium text-fg">Major / Minor</span>: two numbers (0 to 65535) that tell tags apart. Leave blank to match any.
          </li>
        </ul>
        <p>You can read these in the vendor&apos;s configuration app after pairing the tag with your phone. Assign a tag to one person, or leave it shared to cover a whole zone.</p>
      </div>
    </details>
  );
}

export function Beacons() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [params, setParams] = useSearchParams();
  const [office, setOffice] = useState("");
  const f0 = params.get("filter");
  const [filter, setFilter] = useState<Filter>(f0 === "unassigned" || f0 === "inactive" ? f0 : "all");
  const [editing, setEditing] = useState<Beacon | "new" | null>(null);
  const [deleting, setDeleting] = useState<Beacon | null>(null);

  const offices = useOffices();
  const people = usePeople();
  const beacons = useBeacons(office);
  const byId = useMemo(() => new Map((people.data ?? []).map((p) => [p.employee_id, p])), [people.data]);

  const all = beacons.data ?? [];
  const visible = all.filter((b) => (filter === "unassigned" ? !b.assigned_employee_id : filter === "inactive" ? !b.active : true));
  const changeFilter = (f: Filter) => {
    setFilter(f);
    setParams(f === "all" ? {} : { filter: f }, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Beacons"
        description="Desk tags that prove someone is at their desk."
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
            description={all.length ? undefined : "Stick a tag on each desk, then register its UUID, major and minor here so phones know what to listen for."}
            action={!all.length && admin ? <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>Add beacon</Button> : undefined}
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Beacon</Th>
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
                return (
                  <tr key={b.id} className="hover:bg-surface-2/50">
                    <Td>
                      <p className="flex items-center gap-2 font-medium">
                        {b.name}
                        {!b.active && <Badge>Inactive</Badge>}
                      </p>
                      <p className="font-mono text-xs text-muted md:hidden">{shortUuid(b.uuid)}</p>
                    </Td>
                    <Td className="max-md:hidden">
                      <p className="font-mono text-xs" title={b.uuid}>
                        {shortUuid(b.uuid)}
                      </p>
                      <p className="text-xs text-muted tabular-nums">
                        Major {b.major ?? "any"} · Minor {b.minor ?? "any"}
                      </p>
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
        />
      )}
      {deleting && <DeleteDialog beacon={deleting} onClose={() => setDeleting(null)} />}
    </>
  );
}

function BeaconDialog({ beacon, defaultOffice, people, onClose }: { beacon: Beacon | null; defaultOffice: string; people: User[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const offices = useOffices();
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

  const major = parseOptionalInt(f.major);
  const minor = parseOptionalInt(f.minor);
  const uuidErr = f.uuid && !isUuid(f.uuid) ? "Enter a UUID like 2f234454-cf6d-4a0f-adf2-f4911ba9ffa6" : null;
  const candidates = people.filter((p) => p.office_id === f.office_id && (p.active || p.employee_id === f.assigned));

  const m = useMutation({
    mutationFn: () => {
      const body = {
        name: f.name.trim(),
        office_id: f.office_id,
        uuid: f.uuid.trim().toLowerCase(),
        major,
        minor,
        assigned_employee_id: f.assigned || null,
        active: f.active,
      };
      return beacon ? api<Beacon>(`/beacons/${beacon.id}`, { method: "PATCH", body }) : api<Beacon>("/beacons", { method: "POST", body });
    },
    onSuccess: () => {
      toast.success(beacon ? "Beacon saved" : "Beacon added");
      qc.invalidateQueries({ queryKey: ["beacons"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const invalid = !f.name.trim() || !f.office_id || !isUuid(f.uuid) || major === undefined || minor === undefined;

  return (
    <Dialog
      open
      onClose={onClose}
      title={beacon ? `Edit ${beacon.name}` : "Add beacon"}
      description="Copy the identifiers from the tag vendor's configuration app."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" form="beacon-form" variant="primary" loading={m.isPending}>
            {beacon ? "Save changes" : "Add beacon"}
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
        <Field label="Name" hint='For example "Desk 14" or "Floor 2 east".' error={touched && !f.name.trim() ? "Give the beacon a name" : null}>
          {(p) => <Input {...p} value={f.name} onChange={(e) => set("name", e.target.value)} maxLength={120} autoFocus />}
        </Field>
        <Field label="UUID" error={uuidErr ?? (touched && !f.uuid ? "UUID is required" : null)}>
          {(p) => <Input {...p} className="font-mono text-[13px]" value={f.uuid} onChange={(e) => set("uuid", e.target.value)} spellCheck={false} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Major" hint="0 to 65535, blank = any" error={major === undefined ? "Whole number up to 65535" : null}>
            {(p) => <Input {...p} inputMode="numeric" value={f.major} onChange={(e) => set("major", e.target.value)} />}
          </Field>
          <Field label="Minor" hint="0 to 65535, blank = any" error={minor === undefined ? "Whole number up to 65535" : null}>
            {(p) => <Input {...p} inputMode="numeric" value={f.minor} onChange={(e) => set("minor", e.target.value)} />}
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Office">
            {(p) => (
              <Select
                {...p}
                value={f.office_id}
                onChange={(e) => setF((s) => ({ ...s, office_id: e.target.value, assigned: "" }))}
              >
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
