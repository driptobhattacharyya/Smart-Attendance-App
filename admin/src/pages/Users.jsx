import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function Users({ me }) {
  const isAdmin = me.role === "admin";
  const [users, setUsers] = useState([]);
  const [offices, setOffices] = useState([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = () =>
    api(isAdmin ? "/admin/users" : "/manager/team")
      .then(setUsers)
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
    api("/offices").then(setOffices).catch(() => {});
  }, []);

  const officeName = (id) => offices.find((o) => o.id === id)?.name || "—";

  async function update(employeeId, changes) {
    setError("");
    try {
      await api(`/admin/users/${employeeId}`, { method: "PATCH", body: changes });
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function resetFace(u) {
    if (!confirm(`Reset ${u.name}'s face? They will need to register it again in the app.`)) {
      return;
    }
    try {
      await api(`/manager/team/${u.employee_id}/face`, { method: "DELETE" });
      setNotice(`${u.name} can now re-register their face.`);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <section>
      <h2>People</h2>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Email</th>
              <th>Office</th>
              <th>Role</th>
              <th>Face</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const self = u.employee_id === me.employee_id;
              return (
                <tr key={u.employee_id} className={u.active ? "" : "inactive"}>
                  <td>
                    {u.name}
                    <div className="muted small">{u.employee_id}</div>
                  </td>
                  <td>{u.email}</td>
                  <td>
                    {isAdmin && !self ? (
                      <select
                        value={u.office_id || ""}
                        onChange={(e) => update(u.employee_id, { office_id: e.target.value })}
                      >
                        {offices.map((o) => (
                          <option key={o.id} value={o.id}>
                            {o.name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      officeName(u.office_id)
                    )}
                  </td>
                  <td>
                    {isAdmin && !self ? (
                      <select
                        value={u.role}
                        onChange={(e) => update(u.employee_id, { role: e.target.value })}
                      >
                        <option value="employee">Employee</option>
                        <option value="manager">Manager</option>
                        <option value="admin">Admin</option>
                      </select>
                    ) : (
                      u.role
                    )}
                  </td>
                  <td>{u.face_enrolled ? "Registered" : <span className="muted">Not yet</span>}</td>
                  <td>{u.active ? "Active" : "Disabled"}</td>
                  <td className="actions">
                    {u.face_enrolled && !self && (
                      <button className="secondary" onClick={() => resetFace(u)}>
                        Reset face
                      </button>
                    )}
                    {isAdmin && !self && (
                      <button
                        className="secondary"
                        onClick={() => update(u.employee_id, { active: !u.active })}
                      >
                        {u.active ? "Disable" : "Enable"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
