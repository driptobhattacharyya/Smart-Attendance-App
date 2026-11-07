import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

const FILTERS = ["Pending", "Approved", "Rejected", "All"];

function describe(r) {
  const dates = r.start_date === r.end_date ? r.start_date : `${r.start_date} → ${r.end_date}`;
  return r.kind === "correction" ? `${dates}, ${r.entry_time}–${r.exit_time}` : dates;
}

export default function Requests() {
  const [filter, setFilter] = useState("Pending");
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(() => {
    setError("");
    const q = filter === "All" ? "" : `?status=${filter}`;
    api(`/manager/requests${q}`)
      .then(setItems)
      .catch((e) => setError(e.message));
  }, [filter]);

  useEffect(load, [load]);

  async function decide(id, action) {
    setBusyId(id);
    try {
      await api(`/manager/requests/${id}/decision`, { method: "POST", body: { action } });
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section>
      <div className="toolbar">
        <h2>Requests</h2>
        <div className="segmented">
          {FILTERS.map((f) => (
            <button
              key={f}
              className={filter === f ? "active" : ""}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {items.length === 0 ? (
        <p className="muted">Nothing here.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Employee</th>
                <th>Type</th>
                <th>When</th>
                <th>Reason</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id}>
                  <td>
                    {r.employee_name || r.employee_id}
                    <div className="muted small">{r.employee_id}</div>
                  </td>
                  <td>{r.kind === "correction" ? "Correction" : "Leave"}</td>
                  <td>{describe(r)}</td>
                  <td className="reason">{r.reason}</td>
                  <td>
                    <span className={`pill ${r.status.toLowerCase()}`}>{r.status}</span>
                  </td>
                  <td className="actions">
                    {r.status === "Pending" && (
                      <>
                        <button disabled={busyId === r.id} onClick={() => decide(r.id, "approve")}>
                          Approve
                        </button>
                        <button
                          className="secondary"
                          disabled={busyId === r.id}
                          onClick={() => decide(r.id, "reject")}
                        >
                          Reject
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
