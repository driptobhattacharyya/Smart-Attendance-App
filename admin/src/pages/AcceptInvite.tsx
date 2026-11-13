import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useAuth } from "../lib/auth";
import { Button } from "../ui/Button";
import { Field, Input } from "../ui/form";
import { AuthLayout, FormError } from "../ui/AuthLayout";
import { MIN_PASSWORD } from "./Signup";

/** `/invite?token=` (and `/accept-invite`, the path the backend's invitation e-mail links to today). */
export function AcceptInvite() {
  const { acceptInvite } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
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
      await acceptInvite(token, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not accept the invitation.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Join your team on Attendify"
      subtitle="Choose a password to activate your account."
      footer={
        <Link to="/" className="font-medium text-accent-text underline underline-offset-2">
          Already have an account? Sign in
        </Link>
      }
    >
      {!token ? (
        <FormError>This invitation link is incomplete. Open the link from your e-mail again, or ask your administrator to resend it.</FormError>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <FormError>{error}</FormError>}
          <Field label="Password" hint={`At least ${MIN_PASSWORD} characters.`}>
            {(p) => (
              <Input {...p} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={MIN_PASSWORD} maxLength={128} autoFocus required />
            )}
          </Field>
          <Field label="Repeat the password">
            {(p) => <Input {...p} type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />}
          </Field>
          <Button type="submit" variant="primary" loading={busy} className="w-full">
            Activate account
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}
