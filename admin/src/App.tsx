import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";
import { useAuth } from "./lib/auth";
import { Shell } from "./layout/Shell";
import { Login } from "./pages/Login";
import { MobileOnly } from "./pages/MobileOnly";
import { Overview } from "./pages/Overview";
import { Live } from "./pages/Live";
import { Timesheets } from "./pages/Timesheets";
import { Requests } from "./pages/Requests";
import { People } from "./pages/People";
import { Beacons } from "./pages/Beacons";
import { Skeleton } from "./ui/misc";

// The Leaflet map is only needed on this page, so keep it out of the main bundle.
const Offices = lazy(() => import("./pages/Offices").then((m) => ({ default: m.Offices })));

export function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center" role="status" aria-label="Loading">
        <Skeleton className="size-10 rounded-xl" />
      </div>
    );
  }
  if (!user) return <Login />;
  if (user.role === "employee") return <MobileOnly />;

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Overview />} />
        <Route path="live" element={<Live />} />
        <Route path="attendance" element={<Timesheets />} />
        <Route path="requests" element={<Requests />} />
        <Route path="people" element={<People />} />
        <Route path="beacons" element={<Beacons />} />
        <Route
          path="offices"
          element={
            user.role === "admin" ? (
              <Suspense fallback={<Skeleton className="h-64 w-full" />}>
                <Offices />
              </Suspense>
            ) : (
              <Navigate to="/" replace />
            )
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
