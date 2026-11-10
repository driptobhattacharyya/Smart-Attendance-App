import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ClipboardCheck, Clock, Palmtree, X } from "lucide-react";
import { decideRequest, useRequests } from "../api/attendance";
import type { AttendanceRequest } from "../api/attendance";
import { fmtDateTime, fmtRange } from "../lib/format";
import { useToast } from "../lib/toast";
import { useAuth } from "../lib/auth";
import { Avatar, Badge, Card, EmptyState, ErrorState, PageHeader, Skeleton, Tabs } from "../ui/misc";
import { Button } from "../ui/Button";
import { Field, Textarea } from "../ui/form";
import { Dialog } from "../ui/Dialog";

type Tab = "Pending" | "Approved" | "Rejected" | "All";
const STATUS_TONE = { Pending: "warn", Approved: "success", Rejected: "danger" } as const;

export function Requests() {
  const { me } = useAuth();
  const q = useRequests();
  const [tab, setTab] = useState<Tab>("Pending");
  const [deciding, setDeciding] = useState<{ req: AttendanceRequest; action: "approve" | "reject" } | null>(null);

  const all = q.data;
  const counts = useMemo(
    () => ({
      Pending: all?.filter((r) => r.status === "Pending").length ?? 0,
      Approved: all?.filter((r) => r.status === "Approved").length ?? 0,
      Rejected: all?.filter((r) => r.status === "Rejected").length ?? 0,
      All: all?.length ?? 0,
    }),
    [all],
  );
  const visible = useMemo(() => (all ?? []).filter((r) => tab === "All" || r.status === tab), [all, tab]);

  return (
    <>
      <PageHeader
        title="Requests"
        description="Time corrections and leave requests from your team. Approved corrections add worked time; approved leave marks days as on leave."
      />
      <Tabs
        label="Request status"
        value={tab}
        onChange={setTab}
        options={(["Pending", "Approved", "Rejected", "All"] as Tab[]).map((t) => ({
          value: t,
          label: t,
          count: all ? counts[t] : undefined,
        }))}
      />

      <div className="mt-4 space-y-3">
        {q.isLoading ? (
          Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-28 rounded-xl" />)
        ) : q.isError ? (
          <Card>
            <ErrorState error={q.error} onRetry={() => q.refetch()} feature="Requests" />
          </Card>
        ) : visible.length === 0 ? (
          <Card>
            <EmptyState
              icon={<ClipboardCheck />}
              title={tab === "Pending" ? "You are all caught up" : `No ${tab === "All" ? "" : tab.toLowerCase() + " "}requests`}
              description={
                tab === "Pending"
                  ? "New correction and leave requests from your team will show up here for review."
                  : "Requests will appear here once people submit them from the mobile app."
              }
            />
          </Card>
        ) : (
          visible.map((r) => (
            <RequestCard
              key={r.id}
              r={r}
              own={r.user_id === me?.id}
              onDecide={(action) => setDeciding({ req: r, action })}
            />
          ))
        )}
      </div>

      {deciding && <DecisionDialog key={deciding.req.id} {...deciding} onClose={() => setDeciding(null)} />}
    </>
  );
}

function RequestCard({
  r,
  own,
  onDecide,
}: {
  r: AttendanceRequest;
  own: boolean;
  onDecide: (a: "approve" | "reject") => void;
}) {
  const name = r.employee_name ?? "Someone";
  const leave = r.kind === "leave";
  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 gap-3">
          <Avatar name={name} size={36} />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium">{name}</span>
              <span className="text-xs text-muted">{r.employee_code}</span>
              <Badge tone={leave ? "violet" : r.kind === "wfh" ? "accent" : "info"}>
                {leave ? <Palmtree className="size-3" /> : <Clock className="size-3" />}
                {leave ? "Leave" : r.kind === "wfh" ? "Work from home" : "Time correction"}
              </Badge>
              <Badge tone={STATUS_TONE[r.status]} dot>
                {r.status}
              </Badge>
            </div>
            <p className="mt-1 text-sm font-medium tabular-nums">
              {fmtRange(r.start_date, r.end_date)}
              {r.entry_time && r.exit_time && (
                <span className="ml-2 font-normal text-muted">
                  {r.entry_time} – {r.exit_time}
                </span>
              )}
            </p>
            <p className="mt-1.5 text-sm text-pretty break-words text-muted">“{r.reason}”</p>
            <p className="mt-2 text-xs text-subtle">
              Submitted {fmtDateTime(r.submitted_at)}
              {r.decided_by && r.decided_at && ` · ${r.status.toLowerCase()} by ${r.decided_by} on ${fmtDateTime(r.decided_at)}`}
            </p>
            {r.comment && (
              <p className="mt-2 rounded-lg bg-surface-2 px-3 py-2 text-[13px]">
                <span className="text-muted">Comment: </span>
                {r.comment}
              </p>
            )}
          </div>
        </div>
        {r.status === "Pending" && (
          <div className="flex shrink-0 gap-2 max-sm:pl-12">
            {own ? (
              <span className="text-xs text-muted">Your own request: someone else must decide.</span>
            ) : (
              <>
                <Button onClick={() => onDecide("reject")} icon={<X className="size-4" />}>
                  Reject
                </Button>
                <Button variant="primary" onClick={() => onDecide("approve")} icon={<Check className="size-4" />}>
                  Approve
                </Button>
              </>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function DecisionDialog({
  req,
  action,
  onClose,
}: {
  req: AttendanceRequest;
  action: "approve" | "reject";
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [comment, setComment] = useState("");
  const approve = action === "approve";

  const m = useMutation({
    mutationFn: () => decideRequest(req.id, action, comment.trim()),
    onSuccess: () => {
      toast.success(`${approve ? "Approved" : "Rejected"} ${req.employee_name ?? "Someone"}'s request`);
      qc.invalidateQueries({ queryKey: ["requests"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["timesheets"] });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog
      open
      onClose={onClose}
      size="sm"
      title={approve ? "Approve request" : "Reject request"}
      description={`${req.employee_name ?? "Someone"} · ${fmtRange(req.start_date, req.end_date)}`}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            form="decision-form"
            type="submit"
            variant={approve ? "primary" : "danger"}
            loading={m.isPending}
          >
            {approve ? "Approve" : "Reject"}
          </Button>
        </>
      }
    >
      <form
        id="decision-form"
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
        className="space-y-3"
      >
        {approve && req.kind === "correction" && (
          <p className="rounded-lg bg-info-soft px-3 py-2 text-[13px] text-info">
            Approving adds a session of {req.entry_time} – {req.exit_time} to their timesheet.
          </p>
        )}
        {approve && req.kind === "wfh" && (
          <p className="rounded-lg bg-accent-soft px-3 py-2 text-[13px] text-accent-text">
            Approving lets them check in remotely on these days.
          </p>
        )}
        {approve && req.kind === "leave" && (
          <p className="rounded-lg bg-violet-soft px-3 py-2 text-[13px] text-violet">
            Approving marks these days as on leave.
          </p>
        )}
        <Field label="Comment (optional)" hint="Visible to the employee in the app.">
          {(p) => <Textarea {...p} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} autoFocus />}
        </Field>
      </form>
    </Dialog>
  );
}
