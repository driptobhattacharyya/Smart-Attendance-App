import { useEffect, useState } from "react";
import { api, getToken, setToken } from "./api.js";
import Login from "./pages/Login.jsx";
import Attendance from "./pages/Attendance.jsx";
import Requests from "./pages/Requests.jsx";
import Offices from "./pages/Offices.jsx";
import Users from "./pages/Users.jsx";
import Beacons from "./pages/Beacons.jsx";

const TABS = [
  { id: "attendance", label: "Attendance", roles: ["manager", "admin"] },
  { id: "requests", label: "Requests", roles: ["manager", "admin"] },
  { id: "users", label: "People", roles: ["manager", "admin"] },
  { id: "beacons", label: "Beacons", roles: ["manager", "admin"] },
  { id: "offices", label: "Offices", roles: ["admin"] },
];

export default function App() {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(Boolean(getToken()));
  const [tab, setTab] = useState("attendance");

  useEffect(() => {
    if (!getToken()) return;
    api("/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setChecking(false));
  }, []);

  function logout() {
    setToken(null);
    setUser(null);
  }

  if (checking) return <div className="center muted">Loading…</div>;
  if (!user) return <Login onLogin={setUser} />;
  if (!["manager", "admin"].includes(user.role)) {
    return (
      <div className="center">
        <div className="card narrow">
          <h2>Managers only</h2>
          <p className="muted">
            This panel is for managers and admins. Employees use the Attendify mobile app.
          </p>
          <button onClick={logout}>Log out</button>
        </div>
      </div>
    );
  }

  const tabs = TABS.filter((t) => t.roles.includes(user.role));

  return (
    <div className="layout">
      <header className="topbar">
        <strong className="brand">Attendify</strong>
        <nav>
          {tabs.map((t) => (
            <button
              key={t.id}
              className={`tab ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div className="who">
          <span className="muted">
            {user.name} · {user.role}
          </span>
          <button className="link" onClick={logout}>
            Log out
          </button>
        </div>
      </header>
      <main>
        {tab === "attendance" && <Attendance />}
        {tab === "requests" && <Requests />}
        {tab === "users" && <Users me={user} />}
        {tab === "beacons" && <Beacons me={user} />}
        {tab === "offices" && <Offices />}
      </main>
    </div>
  );
}
