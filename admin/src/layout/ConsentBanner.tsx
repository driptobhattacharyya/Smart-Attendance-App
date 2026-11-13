import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { api } from "../api/client";
import type { ConsentText } from "../api/types";
import { useAuth } from "../lib/auth";
import { useToast } from "../lib/toast";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Skeleton } from "../ui/misc";

/**
 * Attendance endpoints answer 403 consent_required until the signed-in user accepts the company's
 * current privacy text, so owners and managers get a nudge here (employees do the same in the app).
 */
export function ConsentBanner() {
  const { me, reload } = useAuth();
  const [open, setOpen] = useState(false);
  if (!me?.consent_required) return null;
  return (
    <>
      <div role="status" className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-warn-soft px-4 py-3 text-sm">
        <ShieldCheck className="size-4 shrink-0 text-warn" aria-hidden />
        <p className="min-w-0 flex-1 text-pretty">
          <span className="font-medium">Review the privacy notice.</span> Attendance data stays hidden from you until you accept how it is collected and used.
        </p>
        <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
          Review and accept
        </Button>
      </div>
      {open && <ConsentDialog version={me.consent_version} onClose={() => setOpen(false)} onAccepted={reload} />}
    </>
  );
}

function ConsentDialog({ version, onClose, onAccepted }: { version: number; onClose: () => void; onAccepted: () => Promise<void> }) {
  const toast = useToast();
  const text = useQuery({ queryKey: ["consent-text", version], queryFn: () => api<ConsentText>("/consent/text") });
  const accept = useMutation({
    mutationFn: async () => {
      await api("/auth/consent", { method: "POST", body: { version: text.data?.version ?? version } });
      await onAccepted();
    },
    onSuccess: () => {
      toast.success("Thank you. Consent recorded.");
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title={text.data?.title ?? "Privacy notice"}
      footer={
        <>
          <Button onClick={onClose}>Not now</Button>
          <Button variant="primary" loading={accept.isPending} disabled={!text.data} onClick={() => accept.mutate()}>
            I accept
          </Button>
        </>
      }
    >
      {text.isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : text.isError ? (
        <p className="text-sm text-danger">{text.error.message}</p>
      ) : (
        <div className="text-sm leading-relaxed whitespace-pre-wrap text-pretty">{text.data?.body_markdown}</div>
      )}
    </Dialog>
  );
}
