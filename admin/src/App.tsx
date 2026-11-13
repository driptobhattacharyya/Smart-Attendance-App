import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router";
import { useAuth } from "./lib/auth";
import { P } from "./api/types";
import { Shell } from "./layout/Shell";
import { Login } from "./pages/Login";
import { Signup } from "./pages/Signup";
import { ForgotPassword, ResetPassword } from "./pages/PasswordPages";
import { AcceptInvite } from "./pages/AcceptInvite";
import { MobileOnly } from "./pages/MobileOnly";
import { Overview } from "./pages/Overview";
import { Live } from "./pages/Live";
import { Timesheets } from "./pages/Timesheets";
import { Requests } from "./pages/Requests";
import { People } from "./pages/People";
import { DeskDevices } from "./pages/DeskDevices";
import { Settings } from "./pages/Settings";
import { AuditLog } from "./pages/AuditLog";
import { Account } from "./pages/Account";
import { Skeleton } from "./ui/misc";

// The Leaflet map is only needed on this page, so keep it out of the main bundle.
const Offices = lazy(() => import("./pages/Offices").then((m) => ({ default: m.Offices })));

/** Renders children only when the user holds one of the permissions; the backend enforces it again. */
function Guard({ any, children }: { any: string[]; children: ReactNode }) {
  const { canAny } = useAuth();
  return canAny(...any) ? <>{children}</> : <Navigate to="/" replace />;
}

export function App() {
  const { me, loading, console: hasConsole } = useAuth();

  return (
    <Routes>
      {/* Reachable signed in or out: following a link replaces whatever session this browser had. */}
      <Route path="invite" element={<AcceptInvite />} />
      <Route path="accept-invite" element={<AcceptInvite />} />
      <Route path="reset-password" element={<ResetPassword />} />
      <Route
        path="*"
        element={
          loading ? (
            <div className="flex min-h-dvh items-center justify-center" role="status" aria-label="Loading">
              <Skeleton className="size-10 rounded-xl" />
            </div>
          ) : !me ? (
            <Routes>
              <Route path="signup" element={<Signup />} />
              <Route path="forgot-password" element={<ForgotPassword />} />
              <Route path="*" element={<Login />} />
            </Routes>
          ) : !hasConsole ? (
            <MobileOnly />
          ) : (
            <Routes>
              <Route element={<Shell />}>
                <Route index element={<Overview />} />
                <Route
                  path="live"
                  element={
                    <Guard any={[P.viewTeam, P.viewOrg]}>
                      <Live />
                    </Guard>
                  }
                />
                <Route
                  path="attendance"
                  element={
                    <Guard any={[P.viewTeam, P.viewOrg]}>
                      <Timesheets />
                    </Guard>
                  }
                />
                <Route
                  path="requests"
                  element={
                    <Guard any={[P.approveTeam, P.approveOrg]}>
                      <Requests />
                    </Guard>
                  }
                />
                <Route
                  path="people"
                  element={
                    <Guard any={[P.peopleManage, P.peopleTeam]}>
                      <People />
                    </Guard>
                  }
                />
                <Route
                  path="desk-devices"
                  element={
                    <Guard any={[P.placesManage]}>
                      <DeskDevices />
                    </Guard>
                  }
                />
                <Route
                  path="offices"
                  element={
                    <Guard any={[P.placesManage]}>
                      <Suspense fallback={<Skeleton className="h-64 w-full" />}>
                        <Offices />
                      </Suspense>
                    </Guard>
                  }
                />
                <Route
                  path="audit"
                  element={
                    <Guard any={[P.audit]}>
                      <AuditLog />
                    </Guard>
                  }
                />
                <Route
                  path="settings"
                  element={
                    <Guard any={[P.orgManage]}>
                      <Settings />
                    </Guard>
                  }
                />
                <Route path="account" element={<Account />} />
                <Route path="beacons" element={<Navigate to="/desk-devices" replace />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Routes>
          )
        }
      />
    </Routes>
  );
}
