import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import { useAuth } from "../lib/auth";
import { Button } from "../ui/Button";
import { Field, Input } from "../ui/form";
import { AuthLayout, FormError } from "../ui/AuthLayout";

export const MIN_PASSWORD = 10;

export function Signup() {
  const { signup } = useAuth();
  const [f, setF] = useState({ org_name: "", name: "", email: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const set = (k: keyof typeof f, v: string) => setF((s) => ({ ...s, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setFields({});
    try {
      await signup({ ...f, org_name: f.org_name.trim(), name: f.name.trim(), email: f.email.trim() });
    } catch (err) {
      if (err instanceof ApiError && err.code === "validation_error" && Object.keys(err.fields).length) setFields(err.fields);
      else setError(err instanceof Error ? err.message : "Could not create the account.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Create your company account"
      subtitle="You will be the owner and can invite your team afterwards."
      footer={
        <>
          Already have an account?{" "}
          <Link to="/" className="font-medium text-accent-text underline underline-offset-2">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        {error && <FormError>{error}</FormError>}
        <Field label="Company name" error={fields.org_name}>
          {(p) => (
            <Input {...p} value={f.org_name} onChange={(e) => set("org_name", e.target.value)} autoComplete="organization" required minLength={2} maxLength={120} autoFocus />
          )}
        </Field>
        <Field label="Your name" error={fields.name}>
          {(p) => <Input {...p} value={f.name} onChange={(e) => set("name", e.target.value)} autoComplete="name" required maxLength={120} />}
        </Field>
        <Field label="Work email" error={fields.email}>
          {(p) => <Input {...p} type="email" value={f.email} onChange={(e) => set("email", e.target.value)} autoComplete="email" required />}
        </Field>
        <Field label="Password" hint={`At least ${MIN_PASSWORD} characters.`} error={fields.password}>
          {(p) => (
            <Input
              {...p}
              type="password"
              value={f.password}
              onChange={(e) => set("password", e.target.value)}
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD}
              maxLength={128}
            />
          )}
        </Field>
        <Button type="submit" variant="primary" loading={busy} className="w-full">
          Create account
        </Button>
      </form>
    </AuthLayout>
  );
}
