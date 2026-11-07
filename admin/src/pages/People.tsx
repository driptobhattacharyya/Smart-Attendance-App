import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bluetooth, Pencil, ScanFace, UserPlus, Users } from "lucide-react";
import { api } from "../lib/api";
import type { Beacon, Office, Role, User } from "../lib/types";
import { officeName, useBeacons, useOffices, usePeople } from "../lib/queries";
import { useAuth } from "../lib/auth";
import { useToast } from "../lib/toast";
import { Avatar, Badge, Card, ChipGroup, EmptyState, ErrorState, PageHeader, Table, TableSkeleton, Td, Th } from "../ui/misc";
import { Button, IconButton } from "../ui/Button";
import { Field, Input, SearchInput, Select, Switch } from "../ui/form";
import { Dialog } from "../ui/Dialog";

type Setup = "all" | "no-face" | "no-beacon" | "inactive";
type BeaconState = "assigned" | "shared" | "none";

const ROLE_TONE = { admin: "accent", manager: "info", employee: "neutral" } as const;

function beaconState(u: User, beacons: Beacon[]): BeaconState {
  const active = beacons.filter((b) => b.active);
  if (active.some((b) => b.assigned_employee_id === u.employee_id)) return "assigned";
  if (active.some((b) => !b.assigned_employee_id && b.office_id === u.office_id)) return "shared";
  return "none";
}

export function People() {
  const { user: me } = useAuth();
  const admin = me?.role === "admin";
  const [params, setParams] = useSearchParams();
  const initial = params.get("setup") as Setup | null;
  const [setup, setSetup] = useState<Setup>(initial && ["no-face", "no-beacon", "inactive"].includes(initial) ? initial : "all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<User | null>(null);
  const [adding, setAdding] = useState(false);
  const [resetting, setResetting] = useState<User | null>(null);

  const people = usePeople();
  const offices = useOffices();
  const beacons = useBeacons();

  const rows = useMemo(() => {
    const list = people.data ?? [];
    return list.map((u) => ({ u, beacon: beaconState(u, beacons.data ?? []) }));
  }, [people.data, beacons.data]);

  const counts = useMemo(
    () => ({
      all: rows.length,
      "no-face": rows.filter((r) => r.u.active && !r.u.face_enrolled).length,
      "no-beacon": rows.filter((r) => r.u.active && r.beacon === "none").length,
      inactive: rows.filter((r) => !r.u.active).length,
    }),
    [rows],
  );

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return rows.filter(({ u, beacon }) => {
      if (setup === "no-face" && (!u.active || u.face_enrolled)) return false;
      if (setup === "no-beacon" && (!u.active || beacon !== "none")) return false;
      if (setup === "inactive" && u.active) return false;
      return !term || [u.name, u.email, u.employee_id].some((v) => v.toLowerCase().includes(term));
    });
  }, [rows, setup, search]);

  const changeSetup = (s: Setup) => {
    setSetup(s);
    setParams(s === "all" ? {} : { setup: s }, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="People"
        description={admin ? "Everyone with an account. Add people, change roles and offices, or reset a face scan." : "Your team. You can reset a face scan so someone can enrol again."}
        actions={
          admin && (
            <Button variant="primary" icon={<UserPlus className="size-4" />} onClick={() => setAdding(true)}>
              Add person
            </Button>
          )
        }
      />

      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <ChipGroup
          label="Filter by setup"
          value={setup}
          onChange={changeSetup}
          options={[
            { value: "all", label: "Everyone", count: counts.all },
            { value: "no-face", label: "No face scan", count: counts["no-face"] },
            { value: "no-beacon", label: "No beacon", count: counts["no-beacon"] },
            { value: "inactive", label: "Disabled", count: counts.inactive },
          ]}
        />
        <SearchInput value={search} onChange={setSearch} placeholder="Search name, email or ID" className="lg:w-72" />
      </div>

      <Card>
        {people.isLoading ? (
          <TableSkeleton rows={8} />
        ) : people.isError ? (
          <ErrorState error={people.error} onRetry={() => people.refetch()} feature="People" />
        ) : visible.length === 0 ? (
          <EmptyState
            icon={<Users />}
            title={rows.length ? "No one matches" : "No people yet"}
            description={rows.length ? "Try clearing the filter or search." : "Add the first person, or ask employees to register in the mobile app."}
            action={
              rows.length ? (
                <Button
                  onClick={() => {
                    changeSetup("all");
                    setSearch("");
                  }}
                >
                  Clear filters
                </Button>
              ) : admin ? (
                <Button variant="primary" icon={<UserPlus className="size-4" />} onClick={() => setAdding(true)}>
                  Add person
                </Button>
              ) : undefined
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Person</Th>
                <Th className="max-sm:hidden">Role</Th>
                <Th className="max-lg:hidden">Office</Th>
                <Th>Face</Th>
                <Th className="max-md:hidden">Beacon</Th>
                <Th className="max-xl:hidden">Consent</Th>
                <Th align="right">
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {visible.map(({ u, beacon }) => (
                <tr key={u.employee_id} className="hover:bg-surface-2/50">
                  <Td>
                    <div className="flex items-center gap-3">
                      <Avatar name={u.name} />
                      <div className="min-w-0">
                        <p className="flex items-center gap-2 font-medium">
                          <span className="truncate">{u.name}</span>
                          {!u.active && <Badge>Disabled</Badge>}
                        </p>
                        <p className="max-w-52 truncate text-xs text-muted sm:max-w-none">
                          {u.employee_id} · {u.email}
                        </p>
                      </div>
                    </div>
                  </Td>
                  <Td className="max-sm:hidden">
                    <Badge tone={ROLE_TONE[u.role]} className="capitalize">
                      {u.role}
                    </Badge>
                  </Td>
                  <Td className="text-muted max-lg:hidden">{officeName(offices.data, u.office_id)}</Td>
                  <Td>
                    {u.face_enrolled ? (
                      <Badge tone="success" dot>
                        Enrolled
                      </Badge>
                    ) : (
                      <Badge tone="warn" dot>
                        Not enrolled
                      </Badge>
                    )}
                  </Td>
                  <Td className="max-md:hidden">
                    {beacon === "assigned" ? (
                      <Badge tone="success" dot>
                        Own beacon
                      </Badge>
                    ) : beacon === "shared" ? (
                      <Badge tone="info" dot>
                        Shared only
                      </Badge>
                    ) : (
                      <Badge tone="warn" dot>
                        None
                      </Badge>
                    )}
                  </Td>
                  <Td className="max-xl:hidden">
                    {u.consent_required === undefined ? (
                      <span className="text-muted">—</span>
                    ) : u.consent_required ? (
                      <Badge tone="warn">Pending</Badge>
                    ) : (
                      <Badge tone="success">Accepted</Badge>
                    )}
                  </Td>
                  <Td align="right">
                    <div className="flex justify-end gap-0.5">
                      {u.face_enrolled && u.employee_id !== me?.employee_id && (
                        <IconButton label={`Reset face scan for ${u.name}`} onClick={() => setResetting(u)}>
                          <ScanFace className="size-4" />
                        </IconButton>
                      )}
                      {admin && (
                        <IconButton label={`Edit ${u.name}`} onClick={() => setEditing(u)}>
                          <Pencil className="size-4" />
                        </IconButton>
                      )}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {beacons.isError && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-muted">
          <Bluetooth className="size-3.5" /> Beacon status could not be loaded, so the Beacon column may be inaccurate.
        </p>
      )}

      {adding && <AddDialog offices={offices.data ?? []} onClose={() => setAdding(false)} />}
      {editing && <EditDialog key={editing.employee_id} user={editing} self={editing.employee_id === me?.employee_id} offices={offices.data ?? []} onClose={() => setEditing(null)} />}
      {resetting && <ResetFaceDialog user={resetting} onClose={() => setResetting(null)} />}
    </>
  );
}

function OfficeSelect({ offices, id, value, onChange, required }: { offices: Office[]; id: string; value: string; onChange: (v: string) => void; required?: boolean }) {
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} required={required}>
      <option value="">{required ? "Select an office…" : "No office"}</option>
      {offices.map((o) => (
        <option key={o.id} value={o.id}>
          {o.name}
        </option>
      ))}
    </Select>
  );
}

function RoleSelect({ id, value, onChange, disabled }: { id: string; value: Role; onChange: (v: Role) => void; disabled?: boolean }) {
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value as Role)} disabled={disabled}>
      <option value="employee">Employee</option>
      <option value="manager">Manager</option>
      <option value="admin">Admin</option>
    </Select>
  );
}

function AddDialog({ offices, onClose }: { offices: Office[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ name: "", email: "", phone: "", password: "", office_id: offices[0]?.id ?? "", role: "employee" as Role });
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));

  const m = useMutation({
    mutationFn: () =>
      api<User>("/admin/users", {
        method: "POST",
        body: { ...f, name: f.name.trim(), email: f.email.trim(), phone: f.phone.trim() || null },
      }),
    onSuccess: (u) => {
      toast.success(`Added ${u.name} (${u.employee_id})`);
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title="Add person"
      description="They can sign in to the mobile app with their email and this password."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" form="add-form" variant="primary" loading={m.isPending}>
            Add person
          </Button>
        </>
      }
    >
      {offices.length === 0 ? (
        <EmptyState title="Create an office first" description="Every person belongs to an office. Add one under Offices, then come back." className="py-6" />
      ) : (
        <form
          id="add-form"
          className="grid gap-4 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            m.mutate();
          }}
        >
          <Field label="Full name" className="sm:col-span-2">
            {(p) => <Input {...p} value={f.name} onChange={(e) => set("name", e.target.value)} required autoFocus />}
          </Field>
          <Field label="Email">
            {(p) => <Input {...p} type="email" value={f.email} onChange={(e) => set("email", e.target.value)} required />}
          </Field>
          <Field label="Phone (optional)">
            {(p) => <Input {...p} type="tel" value={f.phone} onChange={(e) => set("phone", e.target.value)} />}
          </Field>
          <Field label="Office">
            {(p) => <OfficeSelect offices={offices} id={p.id} value={f.office_id} onChange={(v) => set("office_id", v)} required />}
          </Field>
          <Field label="Role">{(p) => <RoleSelect id={p.id} value={f.role} onChange={(v) => set("role", v)} />}</Field>
          <Field label="Temporary password" hint="At least 8 characters." className="sm:col-span-2">
            {(p) => <Input {...p} type="text" autoComplete="off" minLength={8} maxLength={128} value={f.password} onChange={(e) => set("password", e.target.value)} required />}
          </Field>
        </form>
      )}
    </Dialog>
  );
}

function EditDialog({ user, self, offices, onClose }: { user: User; self: boolean; offices: Office[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(user.name);
  const [role, setRole] = useState<Role>(user.role);
  const [office, setOffice] = useState(user.office_id ?? "");
  const [active, setActive] = useState(user.active);

  const m = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {};
      if (name.trim() !== user.name) body.name = name.trim();
      if (role !== user.role) body.role = role;
      if (office && office !== user.office_id) body.office_id = office;
      if (active !== user.active) body.active = active;
      return api<User>(`/admin/users/${user.employee_id}`, { method: "PATCH", body });
    },
    onSuccess: () => {
      toast.success(`Saved ${name.trim()}`);
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Edit ${user.name}`}
      description={`${user.employee_id} · ${user.email}`}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" form="edit-form" variant="primary" loading={m.isPending}>
            Save changes
          </Button>
        </>
      }
    >
      <form
        id="edit-form"
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        <Field label="Full name">{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} required />}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Role" hint={self ? "You cannot change your own role." : undefined}>
            {(p) => <RoleSelect id={p.id} value={role} onChange={setRole} disabled={self} />}
          </Field>
          <Field label="Office" hint="Moving office also moves their team.">
            {(p) => <OfficeSelect offices={offices} id={p.id} value={office} onChange={setOffice} />}
          </Field>
        </div>
        {self ? (
          <p className="text-xs text-muted">You cannot disable your own account.</p>
        ) : (
          <Switch checked={active} onChange={setActive} label="Account active" hint="Disabled people cannot sign in or check in." />
        )}
      </form>
    </Dialog>
  );
}

function ResetFaceDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: () => api(`/manager/team/${user.employee_id}/face`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success(`${user.name} can enrol their face again`);
      qc.invalidateQueries({ queryKey: ["people"] });
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
      title="Reset face scan?"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" loading={m.isPending} onClick={() => m.mutate()}>
            Reset face scan
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted text-pretty">
        This removes {user.name}&apos;s saved face data. They will not be able to check in until they enrol again in the
        mobile app. Only do this when you have confirmed it is really them.
      </p>
    </Dialog>
  );
}
