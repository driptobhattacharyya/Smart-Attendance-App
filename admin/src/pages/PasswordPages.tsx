import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { CheckCircle2 } from "lucide-react";
import { api } from "../api/client";
import { Button } from "../ui/Button";
import { Field, Input } from "../ui/form";
import { AuthLayout, FormError } from "../ui/AuthLayout";
import { MIN_PASSWORD } from "./Signup";

const backToSignIn = (
  <Link to="/" className="font-medium text-accent-text underline underline-offset-2">
    Back to sign in
  </Link>
);

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await api<{ message: string }>("/auth/password/forgot", { method: "POST", auth: false, body: { email: email.trim() } });
      setSent(out.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the e-mail.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Reset your password" subtitle="We will e-mail you a link that works for 60 minutes." footer={backToSignIn}>
      {sent ? (
        <div role="status" className="flex flex-col items-center gap-3 py-2 text-center">
          <span className="flex size-11 items-center justify-center rounded-full bg-success-soft text-success">
            <CheckCircle2 className="size-6" aria-hidden />
          </span>
          <p className="text-sm text-pretty">{sent}</p>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <FormError>{error}</FormError>}
          <Field label="Work email">
            {(p) => <Input {...p} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" autoFocus required />}
          </Field>
          <Button type="submit" variant="primary" loading={busy} className="w-full">
            Send reset link
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}

export function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/auth/password/reset", { method: "POST", auth: false, body: { token, new_password: password } });
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset the password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Choose a new password" footer={backToSignIn}>
      {!token ? (
        <FormError>This reset link is incomplete. Open the link from your e-mail again, or request a new one.</FormError>
      ) : done ? (
        <div role="status" className="flex flex-col items-center gap-3 py-2 text-center">
          <span className="flex size-11 items-center justify-center rounded-full bg-success-soft text-success">
            <CheckCircle2 className="size-6" aria-hidden />
          </span>
          <p className="text-sm">Password updated. All devices were signed out.</p>
          <Link to="/" className="inline-flex h-9 items-center rounded-lg bg-accent px-3.5 text-sm font-medium text-accent-fg">
            Sign in
          </Link>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <FormError>{error}</FormError>}
          <Field label="New password" hint={`At least ${MIN_PASSWORD} characters.`}>
            {(p) => (
              <Input {...p} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={MIN_PASSWORD} maxLength={128} autoFocus required />
            )}
          </Field>
          <Field label="Repeat the password">
            {(p) => <Input {...p} type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />}
          </Field>
          <Button type="submit" variant="primary" loading={busy} className="w-full">
            Update password
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}
