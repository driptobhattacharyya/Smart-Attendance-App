import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router";
import { useAuth } from "../lib/auth";
import { Button } from "../ui/Button";
import { Field, Input } from "../ui/form";
import { Tabs } from "../ui/misc";
import { AuthLayout, FormError } from "../ui/AuthLayout";

type Mode = "email" | "code";

export function Login() {
  const { login } = useAuth();
  const [mode, setMode] = useState<Mode>("email");
  const [email, setEmail] = useState("");
  const [org, setOrg] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(
        mode === "email"
          ? { email: email.trim(), password }
          : { org_slug: org.trim().toLowerCase(), employee_code: code.trim(), password },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Sign in to Attendify"
      subtitle="Manage attendance, people and offices."
      footer={
        <>
          New company?{" "}
          <Link to="/signup" className="font-medium text-accent-text underline underline-offset-2">
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <Tabs
          label="Sign in with"
          value={mode}
          onChange={(m) => {
            setMode(m);
            setError(null);
          }}
          options={[
            { value: "email", label: "Email" },
            { value: "code", label: "Company code" },
          ]}
        />
        {error && <FormError>{error}</FormError>}
        {mode === "email" ? (
          <Field label="Work email">
            {(p) => (
              <Input
                {...p}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                autoFocus
                required
                placeholder="you@company.com"
              />
            )}
          </Field>
        ) : (
          <>
            <Field label="Company code" hint="The short name of your company in Attendify, for example acme.">
              {(p) => (
                <Input
                  {...p}
                  value={org}
                  onChange={(e) => setOrg(e.target.value)}
                  autoComplete="organization"
                  autoCapitalize="none"
                  spellCheck={false}
                  autoFocus
                  required
                />
              )}
            </Field>
            <Field label="Employee ID">
              {(p) => (
                <Input {...p} value={code} onChange={(e) => setCode(e.target.value)} autoComplete="username" required placeholder="E0001" />
              )}
            </Field>
          </>
        )}
        <Field label="Password">
          {(p) => (
            <Input
              {...p}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          )}
        </Field>
        <Button type="submit" variant="primary" loading={busy} className="w-full">
          Sign in
        </Button>
        <p className="text-center text-[13px]">
          <Link to="/forgot-password" className="text-muted underline underline-offset-2 hover:text-fg">
            Forgot your password?
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}
