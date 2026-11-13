import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Gauge, MapPin, ScanFace, Smartphone, Wifi, Palmtree, PencilRuler, Monitor } from "lucide-react";
import { api } from "../api/client";
import type { ConsentText, ModuleKey, Org, OrgPatch } from "../api/types";
import { useAuth } from "../lib/auth";
import { useToast } from "../lib/toast";
import { Badge, Card, CardHeader, ErrorState, PageHeader, Skeleton } from "../ui/misc";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field, Input, Switch } from "../ui/form";

const MODULES: { key: ModuleKey; title: string; icon: ReactNode; body: string }[] = [
  {
    key: "geofence",
    title: "Location check",
    icon: <MapPin />,
    body: "People can only check in while their phone is inside the circle you drew around the office. Time away from the circle is not counted.",
  },
  {
    key: "face",
    title: "Face check",
    icon: <ScanFace />,
    body: "Employees confirm it is really them with a quick face scan and a blink or head turn when they check in. Attendify keeps a mathematical template, never photos.",
  },
  {
    key: "proximity",
    title: "Desk proximity",
    icon: <Wifi />,
    body: "The phone must hear the Bluetooth signal of the desk device. Stepping away from the desk shows as away, even while still in the building.",
  },
  {
    key: "desk_app",
    title: "Desk computers",
    icon: <Monitor />,
    body: "Office PCs running Attendify Desk can act as the desk device, with no hardware to buy. Needed before you can pair a computer.",
  },
  {
    key: "wfh",
    title: "Work from home",
    icon: <Smartphone />,
    body: "Approved work-from-home days let people check in remotely. The office circle and desk signal are skipped, but the face check still applies.",
  },
  {
    key: "regularization",
    title: "Time corrections",
    icon: <PencilRuler />,
    body: "Employees can ask to fix a missed check-in or check-out. A manager reviews it, and approved corrections add worked time.",
  },
  {
    key: "leave",
    title: "Leave requests",
    icon: <Palmtree />,
    body: "Employees request days off in the app. Approved leave shows as On leave instead of absent.",
  },
];

export function Settings() {
  const q = useQuery({ queryKey: ["org"], queryFn: () => api<Org>("/org") });
  return (
    <>
      <PageHeader title="Settings" description="Company details, which features are switched on, and security. Only owners can change these." />
      {q.isLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-80 rounded-xl" />
        </div>
      ) : q.isError ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => q.refetch()} feature="Settings" />
        </Card>
      ) : (
        q.data && <SettingsForm key={q.dataUpdatedAt} org={q.data} />
      )}
    </>
  );
}

function SettingsForm({ org }: { org: Org }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { reload } = useAuth();
  const [name, setName] = useState(org.name);
  const [modules, setModules] = useState<Record<string, boolean>>(() => Object.fromEntries(MODULES.map((m) => [m.key, !!org.modules[m.key]])));
  const [rooted, setRooted] = useState(org.settings.block_rooted_devices ?? true);
  const [desk, setDesk] = useState(org.settings.desk_activity_monitoring ?? false);
  const [retention, setRetention] = useState(String(org.settings.presence_retention_days ?? 90));
  const [error, setError] = useState<string | null>(null);

  const retentionDays = Number(retention);
  const retentionOk = Number.isInteger(retentionDays) && retentionDays >= 1 && retentionDays <= 3650;

  const patch: OrgPatch = {};
  if (name.trim() !== org.name) patch.name = name.trim();
  const changedModules = MODULES.filter((m) => modules[m.key] !== !!org.modules[m.key]);
  if (changedModules.length) patch.modules = Object.fromEntries(changedModules.map((m) => [m.key, modules[m.key]]));
  const settings: NonNullable<OrgPatch["settings"]> = {};
  if (rooted !== (org.settings.block_rooted_devices ?? true)) settings.block_rooted_devices = rooted;
  if (desk !== (org.settings.desk_activity_monitoring ?? false)) settings.desk_activity_monitoring = desk;
  if (retentionOk && retentionDays !== (org.settings.presence_retention_days ?? 90)) settings.presence_retention_days = retentionDays;
  if (Object.keys(settings).length) patch.settings = settings;
  const dirty = Object.keys(patch).length > 0;

  const save = useMutation({
    mutationFn: (body: OrgPatch) => api<Org>("/org", { method: "PATCH", body }),
    onSuccess: async () => {
      await reload(); // the sidebar and device pages read module flags from /people/me
      qc.invalidateQueries({ queryKey: ["org"] });
      qc.invalidateQueries({ queryKey: ["onboarding"] });
      toast.success("Settings saved");
    },
    onError: (e: Error) => setError(e.message),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!retentionOk) {
      setError("Keep presence history for between 1 and 3650 days.");
      return;
    }
    save.mutate(patch);
  };

  return (
    <form onSubmit={submit} className="space-y-6">
      <Card as="section">
        <CardHeader title="Company" description="How your company appears to your team." />
        <div className="grid gap-4 px-4 pb-5 sm:grid-cols-2 sm:px-5">
          <Field label="Company name">{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} required minLength={2} maxLength={120} />}</Field>
          <Field label="Company code" hint="People can sign in with this code and their employee ID.">
            {(p) => <Input {...p} value={org.slug} readOnly disabled className="font-mono" />}
          </Field>
          {org.settings.plan && (
            <p className="text-sm text-muted sm:col-span-2">
              Plan: <span className="font-medium text-fg capitalize">{org.settings.plan.name ?? "trial"}</span>
              {org.settings.plan.max_employees != null && <> · up to {org.settings.plan.max_employees} people (active and invited)</>}
            </p>
          )}
        </div>
      </Card>

      <Card as="section">
        <CardHeader title="Features" description="Switch on what your company needs. You can change your mind at any time." />
        <ul className="divide-y divide-line px-4 pb-2 sm:px-5">
          {MODULES.map((m) => (
            <li key={m.key} className="flex gap-3 py-4">
              <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-muted [&_svg]:size-4" aria-hidden>
                {m.icon}
              </span>
              <div className="min-w-0 flex-1">
                <Switch checked={modules[m.key]} onChange={(v) => setModules((s) => ({ ...s, [m.key]: v }))} label={m.title} hint={m.body} />
              </div>
            </li>
          ))}
        </ul>
      </Card>

      <Card as="section">
        <CardHeader title="Security and privacy" description="Controls that protect attendance records and employee data." />
        <div className="space-y-5 px-4 pb-5 sm:px-5">
          <Switch
            checked={rooted}
            onChange={setRooted}
            label="Block rooted or jailbroken phones"
            hint="Check-ins from phones whose system was modified are refused, because location and Bluetooth readings on them can be faked. Recommended."
          />
          <Switch
            checked={desk}
            onChange={setDesk}
            label="Use desk computer activity"
            hint="Attendify Desk also reports whether the computer is locked or idle, so a locked screen or ten idle minutes count as away. Employees are told in the privacy notice. Off by default."
          />
          <Field label="Keep presence history for (days)" hint="Detailed location and signal samples older than this are deleted. Daily totals are kept.">
            {(p) => <Input {...p} className="max-w-40" inputMode="numeric" value={retention} onChange={(e) => setRetention(e.target.value)} />}
          </Field>
        </div>
      </Card>

      <ConsentCard version={org.consent_version} />

      <div className="sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center gap-3 border-t border-line bg-surface/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <Button type="submit" variant="primary" loading={save.isPending} disabled={!dirty}>
          Save changes
        </Button>
        {dirty && !save.isPending && <span className="text-sm text-muted">You have unsaved changes.</span>}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
      </div>
    </form>
  );
}

function ConsentCard({ version }: { version: number }) {
  const [reading, setReading] = useState(false);
  const [asking, setAsking] = useState(false);
  return (
    <Card as="section">
      <CardHeader
        title="Privacy notice"
        description="People must accept this before attendance records are collected."
        action={<Badge tone="info">Version {version}</Badge>}
      />
      <div className="flex flex-wrap items-center gap-2 px-4 pb-5 sm:px-5">
        <Button icon={<FileText className="size-4" />} onClick={() => setReading(true)}>
          Read the text
        </Button>
        <Button icon={<Gauge className="size-4" />} onClick={() => setAsking(true)}>
          Ask everyone to accept again
        </Button>
        <p className="basis-full text-xs text-muted text-pretty">
          The text is generated from the features you switched on. Ask for a new acceptance after turning on something that collects more, such as desk computer
          activity.
        </p>
      </div>
      {reading && <ConsentTextDialog onClose={() => setReading(false)} />}
      {asking && <BumpDialog onClose={() => setAsking(false)} />}
    </Card>
  );
}

function ConsentTextDialog({ onClose }: { onClose: () => void }) {
  const q = useQuery({ queryKey: ["consent-text"], queryFn: () => api<ConsentText>("/consent/text") });
  return (
    <Dialog open onClose={onClose} size="lg" title={q.data?.title ?? "Privacy notice"} description={q.data ? `Version ${q.data.version}` : undefined} footer={<Button onClick={onClose}>Close</Button>}>
      {q.isLoading ? <Skeleton className="h-40" /> : q.isError ? <p className="text-sm text-danger">{q.error.message}</p> : <div className="text-sm leading-relaxed whitespace-pre-wrap text-pretty">{q.data?.body_markdown}</div>}
    </Dialog>
  );
}

function BumpDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { reload } = useAuth();
  const m = useMutation({
    mutationFn: () => api<Org>("/org", { method: "PATCH", body: { bump_consent_version: true } satisfies OrgPatch }),
    onSuccess: async () => {
      await reload();
      qc.invalidateQueries({ queryKey: ["org"] });
      qc.invalidateQueries({ queryKey: ["consent-text"] });
      toast.success("Everyone will be asked to accept the notice again");
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Dialog
      open
      onClose={onClose}
      size="sm"
      title="Ask everyone to accept again?"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={m.isPending} onClick={() => m.mutate()}>
            Ask again
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted text-pretty">
        The version number goes up by one. Until each person accepts the new version, they cannot check in. This includes you.
      </p>
    </Dialog>
  );
}
