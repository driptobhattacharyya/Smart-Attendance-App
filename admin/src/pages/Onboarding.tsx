import { useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Building2, Check, Monitor, SlidersHorizontal, UserPlus, X } from "lucide-react";
import { api } from "../api/client";
import type { AuditEntry, Page } from "../api/types";
import { P } from "../api/types";
import { useAuth } from "../lib/auth";
import { useDeskDevices, useOffices, usePeopleCount } from "../lib/queries";
import { Card, CardHeader } from "../ui/misc";
import { IconButton } from "../ui/Button";
import { cx } from "../ui/cx";

const DISMISS_KEY = "attendify_onboarding_dismissed";

function readDismissed(orgId: string): boolean {
  try {
    return localStorage.getItem(`${DISMISS_KEY}:${orgId}`) === "1";
  } catch {
    return false;
  }
}

interface Step {
  key: string;
  title: string;
  body: string;
  to: string;
  cta: string;
  icon: ReactNode;
  done: boolean;
  loading: boolean;
  /** Not counted towards completion (for example desk computers while that feature is off). */
  optional?: boolean;
}

/**
 * Getting-started checklist on the Overview. Every tick comes from real data: offices that exist,
 * people beyond the owner, an audited company-settings change, paired desk computers.
 */
export function Onboarding() {
  const { me, can } = useAuth();
  const orgId = me?.org.id ?? "";
  const [dismissed, setDismissed] = useState(() => readDismissed(orgId));

  const manageOffices = can(P.placesManage);
  const managePeople = can(P.peopleManage);
  const manageOrg = can(P.orgManage);
  const deskOn = !!me?.org.modules.desk_app;

  const offices = useOffices();
  const people = usePeopleCount(managePeople);
  const devices = useDeskDevices(false, manageOffices);
  // A saved settings change leaves an `org.update` entry in the audit log (owners can read it).
  const settingsChanged = useQuery({
    queryKey: ["onboarding", "settings-changed"],
    enabled: manageOrg && can(P.audit),
    queryFn: async () => (await api<Page<AuditEntry>>("/audit", { query: { action: "org.update", limit: 1 } })).items.length > 0,
  });

  if (dismissed || (!manageOffices && !managePeople)) return null;

  const steps: Step[] = [];
  if (manageOffices) {
    steps.push({
      key: "office",
      title: "Add your first office",
      body: "Drop a pin and set a radius. People can only check in from inside this circle.",
      to: "/offices",
      cta: "Add an office",
      icon: <Building2 />,
      done: (offices.data?.length ?? 0) > 0,
      loading: offices.isLoading,
    });
  }
  if (managePeople) {
    steps.push({
      key: "people",
      title: "Invite your people",
      body: "Add employees and managers. Each gets an e-mail to choose a password.",
      to: "/people",
      cta: "Invite people",
      icon: <UserPlus />,
      done: (people.data ?? 0) > 1,
      loading: people.isLoading,
    });
  }
  if (manageOrg) {
    steps.push({
      key: "modules",
      title: "Choose your features",
      body: "Face check, desk proximity, work from home, leave. Switch on what fits your company.",
      to: "/settings",
      cta: "Review features",
      icon: <SlidersHorizontal />,
      done: settingsChanged.data === true,
      loading: settingsChanged.isLoading,
    });
  }
  if (manageOffices) {
    steps.push({
      key: "desk",
      title: "Pair desk computers",
      body: deskOn
        ? "Install Attendify Desk on office computers so a desk can prove someone is really there."
        : "Optional. Turn on Desk computers in Settings if you want office PCs to act as the desk signal.",
      to: deskOn ? "/desk-devices" : "/settings",
      cta: deskOn ? "Pair a computer" : "Open settings",
      icon: <Monitor />,
      done: (devices.data ?? []).some((d) => d.kind === "desk_app" && d.paired),
      loading: devices.isLoading,
      optional: !deskOn,
    });
  }

  const counted = steps.filter((s) => !s.optional);
  const doneCount = counted.filter((s) => s.done).length;
  const allDone = counted.length > 0 && doneCount === counted.length;
  const ready = steps.every((s) => !s.loading);

  const dismiss = () => {
    setDismissed(true);
    try {
      localStorage.setItem(`${DISMISS_KEY}:${orgId}`, "1");
    } catch {
      /* the checklist simply reappears next visit */
    }
  };

  return (
    <Card as="section" className="mb-6">
      <CardHeader
        title={allDone ? "You are all set up" : "Get started"}
        description={ready ? `${doneCount} of ${counted.length} steps done` : "Checking your setup…"}
        action={
          <IconButton label="Hide checklist" onClick={dismiss}>
            <X className="size-4" />
          </IconButton>
        }
      />
      <div className="px-4 sm:px-5">
        <div
          role="progressbar"
          aria-label="Setup progress"
          aria-valuemin={0}
          aria-valuemax={counted.length}
          aria-valuenow={doneCount}
          className="h-1.5 overflow-hidden rounded-full bg-surface-2"
        >
          <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${counted.length ? (doneCount / counted.length) * 100 : 0}%` }} />
        </div>
      </div>
      <ol className="grid gap-3 p-4 sm:grid-cols-2 sm:p-5 xl:grid-cols-4">
        {steps.map((s, i) => (
          <li key={s.key} className={cx("flex flex-col rounded-lg border p-3.5", s.done ? "border-line bg-surface-2/50" : "border-line-strong")}>
            <div className="flex items-center gap-2.5">
              <span
                className={cx(
                  "flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold [&_svg]:size-4",
                  s.done ? "bg-success-soft text-success" : "bg-accent-soft text-accent-text",
                )}
                aria-hidden
              >
                {s.done ? <Check /> : i + 1}
              </span>
              <h3 className="text-sm font-semibold">
                {s.title}
                <span className="sr-only">{s.done ? " (done)" : s.optional ? " (optional)" : " (to do)"}</span>
              </h3>
            </div>
            <p className="mt-2 flex-1 text-[13px] text-muted text-pretty">{s.body}</p>
            {!s.done && (
              <Link to={s.to} className="mt-3 text-[13px] font-medium text-accent-text underline underline-offset-2">
                {s.cta}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </Card>
  );
}
