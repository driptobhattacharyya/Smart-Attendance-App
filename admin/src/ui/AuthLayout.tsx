import type { ReactNode } from "react";
import { ClipboardCheck } from "lucide-react";
import { ThemeToggle } from "../layout/Shell";

/** Centered card used by every signed-out page (sign in, sign up, reset, invite). */
export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="relative flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="absolute top-3 right-3">
        <ThemeToggle />
      </div>
      <main className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <span className="mb-4 flex size-11 items-center justify-center rounded-xl bg-accent text-accent-fg shadow-card">
            <ClipboardCheck className="size-6" aria-hidden />
          </span>
          <h1 className="text-xl font-semibold tracking-tight text-balance">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-muted text-pretty">{subtitle}</p>}
        </div>
        <div className="rounded-xl border border-line bg-surface p-5 shadow-card">{children}</div>
        {footer && <div className="mt-6 text-center text-sm text-muted">{footer}</div>}
      </main>
    </div>
  );
}

export function FormError({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
      {children}
    </p>
  );
}
