import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Building, Mail, Pencil, Trash2, UserPlus, Users } from "lucide-react";
import { api } from "../api/client";
import type { Department, Office, User } from "../api/types";
import { P, ROLE_OPTIONS, roleLabel } from "../api/types";
import { departmentName, officeName, useDebounced, useDepartments, useOffices, usePeopleList, usePeopleNames } from "../lib/queries";
import { timeAgo } from "../lib/format";
import { useAuth } from "../lib/auth";
import { useToast } from "../lib/toast";
import { Avatar, Badge, Card, ChipGroup, EmptyState, ErrorState, PageHeader, Table, TableSkeleton, Td, Th } from "../ui/misc";
import type { Tone } from "../ui/misc";
import { Button, IconButton } from "../ui/Button";
import { Field, Input, SearchInput, Select } from "../ui/form";
import { Dialog } from "../ui/Dialog";
import { PersonPicker } from "../ui/PersonPicker";

type StatusFilter = "all" | "active" | "invited" | "disabled";
type Scope = "team" | "direct";

const ROLE_TONE: Record<string, Tone> = { owner: "violet", admin: "accent", manager: "info", employee: "neutral" };
const STATUS_TONE: Record<string, Tone> = { active: "success", invited: "warn", disabled: "neutral" };
const STATUS_LABEL: Record<string, string> = { active: "Active", invited: "Invited", disabled: "Disabled" };

export function People() {
  const { me, can } = useAuth();
  const manage = can(P.peopleManage);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [scope, setScope] = useState<Scope>("team");
  const [search, setSearch] = useState("");
  const [department, setDepartment] = useState("");
  const [office, setOffice] = useState("");
  const [editing, setEditing] = useState<User | null>(null);
  const [inviting, setInviting] = useState(false);
  const [departmentsOpen, setDepartmentsOpen] = useState(false);

  const q = useDebounced(search.trim());
  const filters = {
    q,
    status: status === "all" ? "" : status,
    department_id: department,
    office_id: office,
    manager_id: !manage && scope === "direct" ? me?.id : "",
  };
  const people = usePeopleList(filters);
  const offices = useOffices();
  const departments = useDepartments();

  const rows = useMemo(() => people.data?.pages.flatMap((p) => p.items) ?? [], [people.data]);

  // Resolve "reports to" names. Managers outside the viewer's scope read as 404 and show a dash.
  const managerName = usePeopleNames(rows.map((u) => u.manager_id));

  const filtered = status !== "all" || !!q || !!department || !!office || scope === "direct";
  const clearFilters = () => {
    setStatus("all");
    setSearch("");
    setDepartment("");
    setOffice("");
    setScope("team");
  };

  return (
    <>
      <PageHeader
        title={manage ? "People" : "My team"}
        description={
          manage
            ? "Everyone in your company. Invite people, set roles and reporting lines, and disable accounts when someone leaves."
            : "People who report to you, directly or through someone on your team."
        }
        actions={
          manage && (
            <>
              <Button icon={<Building className="size-4" />} onClick={() => setDepartmentsOpen(true)}>
                Departments
              </Button>
              <Button variant="primary" icon={<UserPlus className="size-4" />} onClick={() => setInviting(true)}>
                Invite person
              </Button>
            </>
          )
        }
      />

      <div className="mb-4 flex flex-col gap-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <ChipGroup
            label="Filter by status"
            value={status}
            onChange={setStatus}
            options={[
              { value: "all", label: "Everyone" },
              { value: "active", label: "Active" },
              { value: "invited", label: "Invited" },
              { value: "disabled", label: "Disabled" },
            ]}
          />
          <SearchInput value={search} onChange={setSearch} placeholder="Search name, email or ID" className="lg:w-72" />
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          {!manage && (
            <ChipGroup
              label="Team scope"
              value={scope}
              onChange={setScope}
              options={[
                { value: "team", label: "Whole team" },
                { value: "direct", label: "Direct reports" },
              ]}
            />
          )}
          {(departments.data?.length ?? 0) > 0 && (
            <Select aria-label="Department" value={department} onChange={(e) => setDepartment(e.target.value)} className="sm:w-48">
              <option value="">All departments</option>
              {departments.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
          )}
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
      </div>

      <Card>
        {people.isLoading ? (
          <TableSkeleton rows={8} />
        ) : people.isError ? (
          <ErrorState error={people.error} onRetry={() => people.refetch()} feature="People" />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Users />}
            title={filtered ? "No one matches" : manage ? "Invite your first person" : "Nobody reports to you yet"}
            description={
              filtered
                ? "Try clearing the filters or search."
                : manage
                  ? "They get an e-mail to set a password and can then sign in to the mobile app."
                  : "People appear here once an administrator sets you as their manager."
            }
            action={
              filtered ? (
                <Button onClick={clearFilters}>Clear filters</Button>
              ) : manage ? (
                <Button variant="primary" icon={<UserPlus className="size-4" />} onClick={() => setInviting(true)}>
                  Invite person
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Person</Th>
                  <Th className="max-sm:hidden">Role</Th>
                  <Th className="max-lg:hidden">Reports to</Th>
                  <Th className="max-xl:hidden">Department</Th>
                  <Th className="max-lg:hidden">Office</Th>
                  <Th>Status</Th>
                  <Th className="max-xl:hidden">Last sign-in</Th>
                  {manage && (
                    <Th align="right">
                      <span className="sr-only">Actions</span>
                    </Th>
                  )}
                </tr>
              </thead>
              <tbody>
                {rows.map((u) => (
                  <tr key={u.id} className="hover:bg-surface-2/50">
                    <Td>
                      <div className="flex items-center gap-3">
                        <Avatar name={u.name} />
                        <div className="min-w-0">
                          <p className="truncate font-medium">{u.name}</p>
                          <p className="max-w-52 truncate text-xs text-muted sm:max-w-none">
                            {u.employee_code} · {u.email}
                          </p>
                        </div>
                      </div>
                    </Td>
                    <Td className="max-sm:hidden">
                      <Badge tone={ROLE_TONE[u.role_key] ?? "neutral"}>{roleLabel(u.role_key)}</Badge>
                    </Td>
                    <Td className="text-muted max-lg:hidden">{u.manager_id ? (managerName.get(u.manager_id) ?? "—") : "—"}</Td>
                    <Td className="text-muted max-xl:hidden">{departmentName(departments.data, u.department_id)}</Td>
                    <Td className="text-muted max-lg:hidden">{officeName(offices.data, u.office_id)}</Td>
                    <Td>
                      <Badge tone={STATUS_TONE[u.status] ?? "neutral"} dot>
                        {STATUS_LABEL[u.status] ?? u.status}
                      </Badge>
                    </Td>
                    <Td className="text-muted max-xl:hidden">{u.last_login_at ? timeAgo(u.last_login_at) : "Never"}</Td>
                    {manage && (
                      <Td align="right">
                        <IconButton label={`Edit ${u.name}`} onClick={() => setEditing(u)}>
                          <Pencil className="size-4" />
                        </IconButton>
                      </Td>
                    )}
                  </tr>
                ))}
              </tbody>
            </Table>
            {people.hasNextPage && (
              <div className="flex justify-center border-t border-line p-3">
                <Button loading={people.isFetchingNextPage} onClick={() => people.fetchNextPage()}>
                  Load more
                </Button>
              </div>
            )}
          </>
        )}
      </Card>

      {inviting && <InviteDialog offices={offices.data ?? []} departments={departments.data ?? []} onClose={() => setInviting(false)} />}
      {editing && (
        <EditDialog
          key={editing.id}
          user={editing}
          self={editing.id === me?.id}
          offices={offices.data ?? []}
          departments={departments.data ?? []}
          onClose={() => setEditing(null)}
        />
      )}
      {departmentsOpen && <DepartmentsDialog onClose={() => setDepartmentsOpen(false)} />}
    </>
  );
}

function RoleSelect({ id, value, onChange, disabled }: { id: string; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const { can } = useAuth();
  // Only owners may grant (or touch) the owner role; the server enforces the same rule.
  const options = ROLE_OPTIONS.filter((r) => r.key !== "owner" || can(P.orgManage) || value === "owner");
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
      {options.map((r) => (
        <option key={r.key} value={r.key}>
          {r.label}
        </option>
      ))}
    </Select>
  );
}

function OptionSelect({
  id,
  value,
  onChange,
  items,
  empty,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  items: { id: string; name: string }[];
  empty: string;
}) {
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{empty}</option>
      {items.map((o) => (
        <option key={o.id} value={o.id}>
          {o.name}
        </option>
      ))}
    </Select>
  );
}

function invalidatePeople(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["people"] });
  qc.invalidateQueries({ queryKey: ["dashboard"] });
}

function ErrorBanner({ children }: { children: string | null }) {
  if (!children) return null;
  return (
    <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
      {children}
    </p>
  );
}

function InviteDialog({ offices, departments, onClose }: { offices: Office[]; departments: Department[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ name: "", email: "", employee_code: "", phone: "", role_key: "employee", office_id: offices[0]?.id ?? "", department_id: "" });
  const [manager, setManager] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));

  const m = useMutation({
    mutationFn: () =>
      api<User>("/people", {
        method: "POST",
        body: {
          name: f.name.trim(),
          email: f.email.trim(),
          employee_code: f.employee_code.trim() || undefined,
          phone: f.phone.trim() || undefined,
          role_key: f.role_key,
          office_id: f.office_id || undefined,
          department_id: f.department_id || undefined,
          manager_id: manager ?? undefined,
        },
      }),
    onSuccess: (u) => {
      toast.success(`Invitation sent to ${u.email}`);
      invalidatePeople(qc);
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title="Invite person"
      description="They get an e-mail with a link to choose a password. The link works for 7 days."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" form="invite-form" variant="primary" loading={m.isPending} icon={<Mail className="size-4" />}>
            Send invitation
          </Button>
        </>
      }
    >
      <form
        id="invite-form"
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          setError(null);
          m.mutate();
        }}
      >
        {error && (
          <div className="sm:col-span-2">
            <ErrorBanner>{error}</ErrorBanner>
          </div>
        )}
        <Field label="Full name" className="sm:col-span-2">
          {(p) => <Input {...p} value={f.name} onChange={(e) => set("name", e.target.value)} required maxLength={120} autoFocus />}
        </Field>
        <Field label="Work email">
          {(p) => <Input {...p} type="email" value={f.email} onChange={(e) => set("email", e.target.value)} required />}
        </Field>
        <Field label="Employee ID (optional)" hint="Left blank, one is generated.">
          {(p) => <Input {...p} value={f.employee_code} onChange={(e) => set("employee_code", e.target.value)} maxLength={40} />}
        </Field>
        <Field label="Role" hint={ROLE_OPTIONS.find((r) => r.key === f.role_key)?.hint}>
          {(p) => <RoleSelect id={p.id} value={f.role_key} onChange={(v) => set("role_key", v)} />}
        </Field>
        <Field label="Phone (optional)">
          {(p) => <Input {...p} type="tel" value={f.phone} onChange={(e) => set("phone", e.target.value)} maxLength={40} />}
        </Field>
        <Field label="Office">
          {(p) => <OptionSelect id={p.id} value={f.office_id} onChange={(v) => set("office_id", v)} items={offices} empty="No office yet" />}
        </Field>
        <Field label="Department">
          {(p) => <OptionSelect id={p.id} value={f.department_id} onChange={(v) => set("department_id", v)} items={departments} empty="No department" />}
        </Field>
        <Field label="Reports to" className="sm:col-span-2">
          {(p) => <PersonPicker id={p.id} value={manager} onChange={setManager} emptyLabel="No manager" />}
        </Field>
      </form>
    </Dialog>
  );
}

function EditDialog({
  user,
  self,
  offices,
  departments,
  onClose,
}: {
  user: User;
  self: boolean;
  offices: Office[];
  departments: Department[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const [f, setF] = useState({
    name: user.name,
    email: user.email,
    employee_code: user.employee_code,
    phone: user.phone ?? "",
    role_key: user.role_key,
    office_id: user.office_id ?? "",
    department_id: user.department_id ?? "",
  });
  const [manager, setManager] = useState<string | null>(user.manager_id);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));
  const ownerLocked = user.role_key === "owner" && !can(P.orgManage);

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {};
      if (f.name.trim() !== user.name) body.name = f.name.trim();
      if (f.email.trim() !== user.email) body.email = f.email.trim();
      if (f.employee_code.trim() !== user.employee_code) body.employee_code = f.employee_code.trim();
      if ((f.phone.trim() || null) !== user.phone) body.phone = f.phone.trim() || null;
      if (f.role_key !== user.role_key) body.role_key = f.role_key;
      if ((f.office_id || null) !== user.office_id) body.office_id = f.office_id || null;
      if ((f.department_id || null) !== user.department_id) body.department_id = f.department_id || null;
      if (manager !== user.manager_id) body.manager_id = manager;
      return api<User>(`/people/${user.id}`, { method: "PATCH", body });
    },
    onSuccess: (u) => {
      toast.success(`Saved ${u.name}`);
      invalidatePeople(qc);
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  const action = useMutation({
    mutationFn: ({ path, msg }: { path: string; msg: string }) => api(`/people/${user.id}/${path}`, { method: "POST" }).then(() => msg),
    onSuccess: (msg) => {
      toast.success(msg);
      invalidatePeople(qc);
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });
  const run = (path: string, msg: string) => {
    setError(null);
    action.mutate({ path, msg });
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Edit ${user.name}`}
      description={`${user.employee_code} · ${STATUS_LABEL[user.status] ?? user.status}`}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" form="edit-form" variant="primary" loading={save.isPending}>
            Save changes
          </Button>
        </>
      }
    >
      <form
        id="edit-form"
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          setError(null);
          save.mutate();
        }}
      >
        {error && (
          <div className="sm:col-span-2">
            <ErrorBanner>{error}</ErrorBanner>
          </div>
        )}
        <Field label="Full name">{(p) => <Input {...p} value={f.name} onChange={(e) => set("name", e.target.value)} required maxLength={120} />}</Field>
        <Field label="Employee ID">
          {(p) => <Input {...p} value={f.employee_code} onChange={(e) => set("employee_code", e.target.value)} required maxLength={40} />}
        </Field>
        <Field label="Email" hint={user.status === "invited" ? "Fixing a typo? Save, then resend the invitation." : undefined}>
          {(p) => <Input {...p} type="email" value={f.email} onChange={(e) => set("email", e.target.value)} required />}
        </Field>
        <Field label="Phone">
          {(p) => <Input {...p} type="tel" value={f.phone} onChange={(e) => set("phone", e.target.value)} maxLength={40} />}
        </Field>
        <Field
          label="Role"
          hint={self ? "You cannot change your own role." : ownerLocked ? "Only an owner can change an owner." : ROLE_OPTIONS.find((r) => r.key === f.role_key)?.hint}
        >
          {(p) => <RoleSelect id={p.id} value={f.role_key} onChange={(v) => set("role_key", v)} disabled={self || ownerLocked} />}
        </Field>
        <Field label="Reports to">
          {(p) => <PersonPicker id={p.id} value={manager} onChange={setManager} excludeId={user.id} emptyLabel="No manager" />}
        </Field>
        <Field label="Office">
          {(p) => <OptionSelect id={p.id} value={f.office_id} onChange={(v) => set("office_id", v)} items={offices} empty="No office" />}
        </Field>
        <Field label="Department">
          {(p) => <OptionSelect id={p.id} value={f.department_id} onChange={(v) => set("department_id", v)} items={departments} empty="No department" />}
        </Field>
      </form>

      {!self && !ownerLocked && (
        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
          {user.status === "invited" && (
            <Button size="sm" loading={action.isPending} icon={<Mail className="size-4" />} onClick={() => run("invite", "Invitation sent again")}>
              Resend invitation
            </Button>
          )}
          {user.status !== "disabled" && (
            <Button size="sm" loading={action.isPending} onClick={() => run("revoke-sessions", "Signed out of every device")}>
              Sign out everywhere
            </Button>
          )}
          {user.status === "disabled" ? (
            <Button size="sm" variant="secondary" loading={action.isPending} onClick={() => run("enable", `${user.name} can sign in again`)}>
              Enable account
            </Button>
          ) : (
            <Button size="sm" variant="danger" loading={action.isPending} onClick={() => run("disable", `${user.name} was disabled`)}>
              Disable account
            </Button>
          )}
          <p className="basis-full text-xs text-muted text-pretty">
            Disabling signs them out everywhere and cancels pending links. Their records stay. Active and invited people count against your plan&apos;s seat limit.
          </p>
        </div>
      )}
    </Dialog>
  );
}

function DepartmentsDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const deps = useDepartments();
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["departments"] });
    qc.invalidateQueries({ queryKey: ["people"] });
  };
  const create = useMutation({
    mutationFn: () => api<Department>("/departments", { method: "POST", body: { name: name.trim() } }),
    onSuccess: () => {
      setName("");
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const rename = useMutation({
    mutationFn: (d: { id: string; name: string }) => api<Department>(`/departments/${d.id}`, { method: "PATCH", body: { name: d.name.trim() } }),
    onSuccess: () => {
      setRenaming(null);
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/departments/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Department deleted. Its members now have no department.");
      refresh();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog open onClose={onClose} title="Departments" description="Group people for filtering and reports." footer={<Button onClick={onClose}>Done</Button>}>
      <form
        className="mb-4 flex gap-2"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (name.trim()) create.mutate();
        }}
      >
        <Input aria-label="New department name" value={name} onChange={(e) => setName(e.target.value)} placeholder="New department, e.g. Engineering" maxLength={120} />
        <Button type="submit" variant="primary" loading={create.isPending} disabled={!name.trim()}>
          Add
        </Button>
      </form>
      {deps.isLoading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : deps.data?.length === 0 ? (
        <p className="rounded-lg bg-surface-2 px-3 py-4 text-center text-sm text-muted">No departments yet.</p>
      ) : (
        <ul className="divide-y divide-line rounded-lg border border-line">
          {deps.data?.map((d) => (
            <li key={d.id} className="flex items-center gap-2 px-3 py-2">
              {renaming?.id === d.id ? (
                <form
                  className="flex flex-1 gap-2"
                  onSubmit={(e: FormEvent) => {
                    e.preventDefault();
                    if (renaming.name.trim()) rename.mutate(renaming);
                  }}
                >
                  <Input aria-label={`Rename ${d.name}`} value={renaming.name} onChange={(e) => setRenaming({ id: d.id, name: e.target.value })} autoFocus maxLength={120} />
                  <Button size="sm" type="submit" variant="primary" loading={rename.isPending}>
                    Save
                  </Button>
                  <Button size="sm" onClick={() => setRenaming(null)}>
                    Cancel
                  </Button>
                </form>
              ) : (
                <>
                  <span className="min-w-0 flex-1 truncate text-sm">{d.name}</span>
                  <IconButton label={`Rename ${d.name}`} onClick={() => setRenaming({ id: d.id, name: d.name })}>
                    <Pencil className="size-4" />
                  </IconButton>
                  <IconButton label={`Delete ${d.name}`} onClick={() => remove.mutate(d.id)}>
                    <Trash2 className="size-4" />
                  </IconButton>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
