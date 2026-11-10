import { Download, Smartphone } from "lucide-react";
import { useAuth } from "../lib/auth";
import { Button } from "../ui/Button";
import { ThemeToggle } from "../layout/Shell";

export function MobileOnly() {
  const { me, logout } = useAuth();
  return (
    <div className="relative flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="absolute top-3 right-3">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-md rounded-xl border border-line bg-surface p-8 text-center shadow-card">
        <span className="mx-auto mb-4 flex size-12 items-center justify-center rounded-xl bg-accent-soft text-accent-text">
          <Smartphone className="size-6" aria-hidden />
        </span>
        <h1 className="text-lg font-semibold">Hi {me?.name.split(" ")[0]}, attendance lives in the app</h1>
        <p className="mt-2 text-sm text-muted text-pretty">
          This console is for people who manage a team or the company. To check in, view your hours or request a correction, use the
          Attendify mobile app on your phone.
        </p>
        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
          <a href="/download/">
            <Button variant="primary" icon={<Download className="size-4" />} className="w-full">
              Get the mobile app
            </Button>
          </a>
          <Button onClick={() => void logout()}>Sign out</Button>
        </div>
      </div>
    </div>
  );
}
