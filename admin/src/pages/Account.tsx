import { useState } from "react";
import type { FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, MonitorSmartphone, ShieldCheck } from "lucide-react";
import { api } from "../api/client";
import type { Me, Session } from "../api/types";
import { roleLabel } from "../api/types";
import { useAuth } from "../lib/auth";
import { timeAgo } from "../lib/format";
import { useToast } from "../lib/toast";
import { Badge, Card, CardHeader, ErrorState, PageHeader, Skeleton } from "../ui/misc";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field, Input } from "../ui/form";
import { MIN_PASSWORD } from "./Signup";

export function Account() {
  return (
    <>
      <PageHeader title="Account" description="Your profile, password and the devices signed in as you." />
      <div className="grid gap-6 xl:grid-cols-2">
        <div className="min-w-0 space-y-6">
          <Profile />
          <ChangePassword />
        </div>
        <Sessions />
      </div>
    </>
  );
}

function Profile() {
  const { me, reload } = useAuth();
  const toast = useToast();
  const [name, setName] = useState(me?.name ?? "");
  const [phone, setPhone] = useState(me?.phone ?? "");
  const m = useMutation({
    mutationFn: () => api<Me>("/people/me", { method: "PATCH", body: { name: name.trim(), phone: phone.trim() || null } }),
    onSuccess: async () => {
      await reload();
      toast.success("Profile saved");
    },
    onError: (e: Error) => toast.error(e.message),
  });
  if (!me) return null;
  const dirty = name.trim() !== me.name || (phone.trim() || null) !== me.phone;
  return (
    <Card as="section">
      <CardHeader title="Profile" description={`${me.org.name} · ${roleLabel(me.role_key)} · ID ${me.employee_code}`} />
      <form
        className="grid gap-4 px-4 pb-5 sm:grid-cols-2 sm:px-5"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        <Field label="Full name">{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />}</Field>
        <Field label="Phone">{(p) => <Input {...p} type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} maxLength={40} />}</Field>
        <Field label="Email" hint="Ask an administrator to change your sign-in email." className="sm:col-span-2">
          {(p) => <Input {...p} value={me.email} readOnly disabled />}
        </Field>
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" loading={m.isPending} disabled={!dirty}>
            Save profile
          </Button>
        </div>
      </form>
    </Card>
  );
}

function ChangePassword() {
  const toast = useToast();
  const qc = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: () => api<{ message: string }>("/auth/password/change", { method: "POST", body: { current_password: current, new_password: next } }),
    onSuccess: (r) => {
      toast.success(r.message);
      setCurrent("");
      setNext("");
      setConfirm("");
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (e: Error) => setError(e.message),
  });
  return (
    <Card as="section">
      <CardHeader title="Change password" description="Other devices are signed out when you change it." />
      <form
        className="space-y-4 px-4 pb-5 sm:px-5"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          setError(null);
          if (next !== confirm) setError("The two new passwords do not match.");
          else m.mutate();
        }}
      >
        {error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
            {error}
          </p>
        )}
        <Field label="Current password">
          {(p) => <Input {...p} type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="New password" hint={`At least ${MIN_PASSWORD} characters.`}>
            {(p) => (
              <Input {...p} type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={MIN_PASSWORD} maxLength={128} required />
            )}
          </Field>
          <Field label="Repeat new password">
            {(p) => <Input {...p} type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />}
          </Field>
        </div>
        <Button type="submit" variant="primary" loading={m.isPending}>
          Change password
        </Button>
      </form>
    </Card>
  );
}

function Sessions() {
  const { logoutAll } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [confirmAll, setConfirmAll] = useState(false);
  const q = useQuery({ queryKey: ["sessions"], queryFn: () => api<Session[]>("/auth/sessions") });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/auth/sessions/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Device signed out");
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const all = useMutation({
    mutationFn: () => logoutAll(),
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Card as="section" className="min-w-0 self-start">
      <CardHeader
        title="Where you are signed in"
        description="Each browser or phone that holds a sign-in. Sign out any you do not recognise."
        action={
          <Button size="sm" icon={<LogOut className="size-4" />} onClick={() => setConfirmAll(true)}>
            Sign out everywhere
          </Button>
        }
      />
      <div className="px-4 pb-4 sm:px-5">
        {q.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-16" />
            <Skeleton className="h-16" />
          </div>
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} feature="Sessions" />
        ) : (
          <ul className="divide-y divide-line" aria-label="Active sessions">
            {q.data?.map((s) => (
              <li key={s.id} className="flex items-center gap-3 py-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-muted">
                  <MonitorSmartphone className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    <span className="truncate">{s.device_label === "signup" ? "Browser used to sign up" : (s.device_label ?? "Unknown device")}</span>
                    {s.current && <Badge tone="success">This device</Badge>}
                  </p>
                  <p className="truncate text-xs text-muted" title={s.user_agent ?? undefined}>
                    {s.ip ?? "Unknown IP"} · active {timeAgo(s.last_used_at)} · signed in{" "}
                    {new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(new Date(s.created_at))}
                  </p>
                </div>
                {!s.current && (
                  <Button size="sm" loading={revoke.isPending && revoke.variables === s.id} onClick={() => revoke.mutate(s.id)}>
                    Sign out
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 flex items-center gap-1.5 text-xs text-muted">
          <ShieldCheck className="size-3.5" aria-hidden /> Sessions expire after 30 days without use.
        </p>
      </div>
      {confirmAll && (
        <Dialog
          open
          onClose={() => setConfirmAll(false)}
          size="sm"
          title="Sign out everywhere?"
          footer={
            <>
              <Button onClick={() => setConfirmAll(false)}>Cancel</Button>
              <Button variant="danger" loading={all.isPending} onClick={() => all.mutate()}>
                Sign out everywhere
              </Button>
            </>
          }
        >
          <p className="text-sm text-muted text-pretty">
            Every browser and phone signed in as you, including this one, will need to sign in again.
          </p>
        </Dialog>
      )}
    </Card>
  );
}
