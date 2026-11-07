import { useState } from "react";
import type { FormEvent } from "react";
import { ClipboardCheck, ShieldCheck } from "lucide-react";
import { useAuth } from "../lib/auth";
import { Button } from "../ui/Button";
import { Field, Input } from "../ui/form";
import { ThemeToggle } from "../layout/Shell";

export function Login() {
  const { login } = useAuth();
  const [id, setId] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(id, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="absolute top-3 right-3">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="mb-4 flex size-11 items-center justify-center rounded-xl bg-accent text-accent-fg shadow-card">
            <ClipboardCheck className="size-6" aria-hidden />
          </span>
          <h1 className="text-xl font-semibold tracking-tight">Sign in to Attendify</h1>
          <p className="mt-1 text-sm text-muted">Manage attendance, people and offices.</p>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-xl border border-line bg-surface p-5 shadow-card">
          {error && (
            <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}
          <Field label="Employee ID or email">
            {(p) => (
              <Input
                {...p}
                value={id}
                onChange={(e) => setId(e.target.value)}
                autoComplete="username"
                autoFocus
                required
                placeholder="EMP0001 or you@company.com"
              />
            )}
          </Field>
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
        </form>
        <p className="mt-6 flex items-center justify-center gap-1.5 text-xs text-muted">
          <ShieldCheck className="size-3.5" aria-hidden /> Restricted to managers and admins
        </p>
      </div>
    </div>
  );
}
