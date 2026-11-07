import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import {
  Bluetooth,
  Building2,
  CalendarDays,
  ClipboardCheck,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Radio,
  Sun,
  Users,
} from "lucide-react";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/theme";
import { useRequests } from "../lib/queries";
import { Avatar } from "../ui/misc";
import { Dialog } from "../ui/Dialog";
import { IconButton } from "../ui/Button";
import { cx } from "../ui/cx";

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex size-7 items-center justify-center rounded-lg bg-accent text-accent-fg shadow-card">
        <ClipboardCheck className="size-4" aria-hidden />
      </span>
      <span className="text-[15px] font-semibold tracking-tight">Attendify</span>
    </div>
  );
}

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  const requests = useRequests();
  const pending = requests.data?.filter((r) => r.status === "Pending").length ?? 0;

  const items: { to: string; label: string; icon: ReactNode; end?: boolean; badge?: number; admin?: boolean }[] = [
    { to: "/", label: "Overview", icon: <LayoutDashboard />, end: true },
    { to: "/live", label: "Live", icon: <Radio /> },
    { to: "/attendance", label: "Timesheets", icon: <CalendarDays /> },
    { to: "/requests", label: "Requests", icon: <ClipboardCheck />, badge: pending },
    { to: "/people", label: "People", icon: <Users /> },
    { to: "/beacons", label: "Beacons", icon: <Bluetooth /> },
    { to: "/offices", label: "Offices", icon: <Building2 />, admin: true },
  ];

  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {items
        .filter((i) => !i.admin || user?.role === "admin")
        .map((i) => (
          <NavLink
            key={i.to}
            to={i.to}
            end={i.end}
            onClick={onNavigate}
            className={({ isActive }) =>
              cx(
                "flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-sm font-medium transition-colors [&_svg]:size-4",
                isActive
                  ? "bg-accent-soft text-accent-text"
                  : "text-muted hover:bg-surface-2 hover:text-fg",
              )
            }
          >
            {i.icon}
            <span className="flex-1">{i.label}</span>
            {!!i.badge && (
              <span className="rounded-full bg-warn-soft px-1.5 text-xs font-semibold text-warn tabular-nums">
                {i.badge}
              </span>
            )}
          </NavLink>
        ))}
    </nav>
  );
}

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <IconButton label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} onClick={toggle}>
      {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </IconButton>
  );
}

function UserCard() {
  const { user, logout } = useAuth();
  if (!user) return null;
  return (
    <div className="flex items-center gap-2.5 border-t border-line pt-3">
      <Avatar name={user.name} size={32} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-medium">{user.name}</p>
        <p className="truncate text-xs text-muted capitalize">{user.role}</p>
      </div>
      <ThemeToggle />
      <IconButton label="Sign out" onClick={logout}>
        <LogOut className="size-4" />
      </IconButton>
    </div>
  );
}

export function Shell() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();

  useEffect(() => {
    document.getElementById("main")?.focus({ preventScroll: true });
  }, [pathname]);

  return (
    <div className="min-h-dvh lg:pl-60">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2 focus:shadow-pop"
      >
        Skip to content
      </a>

      <aside className="fixed inset-y-0 left-0 hidden w-60 flex-col gap-6 border-r border-line bg-surface p-4 lg:flex">
        <Logo />
        <div className="flex-1">
          <NavItems />
        </div>
        <UserCard />
      </aside>

      <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-line bg-surface/90 px-3 backdrop-blur lg:hidden">
        <IconButton label="Open menu" onClick={() => setOpen(true)}>
          <Menu className="size-5" />
        </IconButton>
        <Logo />
        <div className="ml-auto">
          <ThemeToggle />
        </div>
      </header>

      <Dialog open={open} onClose={() => setOpen(false)} variant="drawer" bare>
        <div className="flex h-full min-h-[calc(100dvh-1px)] flex-col gap-6 p-4">
          <Logo />
          <div className="flex-1">
            <NavItems onNavigate={() => setOpen(false)} />
          </div>
          <UserCard />
        </div>
      </Dialog>

      <main id="main" tabIndex={-1} className="mx-auto max-w-[1280px] px-4 py-6 outline-none sm:px-6 lg:px-8 lg:py-8">
        <Outlet />
      </main>
    </div>
  );
}
