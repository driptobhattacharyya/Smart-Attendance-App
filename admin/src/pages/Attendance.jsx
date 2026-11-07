import { useEffect, useMemo, useState } from "react";
import { api, formatMinutes, isoDate } from "../api.js";

function lastNDays(n) {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - (n - 1));
  return { start: isoDate(start), end: isoDate(end) };
}

function datesBetween(start, end) {
  const out = [];
  const d = new Date(`${start}T00:00:00`);
  const last = new Date(`${end}T00:00:00`);
  while (d <= last) {
    out.push(isoDate(d));
    d.setDate(d.getDate() + 1);
  }
  return out;
}

export default function Attendance() {
  const [range, setRange] = useState(() => lastNDays(7));
  const [reports, setReports] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setError("");
    api(`/manager/attendance?start=${range.start}&end=${range.end}`)
      .then(setReports)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [range]);

  const days = useMemo(() => datesBetween(range.start, range.end), [range]);

  return (
    <section>
      <div className="toolbar">
        <h2>Team attendance</h2>
        <label className="inline">
          From
          <input
            type="date"
            value={range.start}
            onChange={(e) => setRange({ ...range, start: e.target.value })}
          />
        </label>
        <label className="inline">
          To
          <input
            type="date"
            value={range.end}
            onChange={(e) => setRange({ ...range, end: e.target.value })}
          />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      {loading ? (
        <p className="muted">Loading…</p>
      ) : reports.length === 0 ? (
        <p className="muted">No team members yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Employee</th>
                {days.map((d) => (
                  <th key={d} className="num">
                    {new Date(`${d}T00:00:00`).toLocaleDateString(undefined, {
                      weekday: "short",
                      day: "numeric",
                    })}
                  </th>
                ))}
                <th className="num">Total</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => {
                const byDay = Object.fromEntries(r.days.map((d) => [d.date, d]));
                return (
                  <tr key={r.employee_id}>
                    <td>
                      {r.name}
                      <div className="muted small">{r.employee_id}</div>
                    </td>
                    {days.map((d) => {
                      const day = byDay[d];
                      const open = day?.sessions.some((s) => !s.ended_at);
                      return (
                        <td key={d} className="num">
                          {day?.on_leave && !day.total_minutes ? (
                            <span className="pill leave">Leave</span>
                          ) : (
                            <>
                              {formatMinutes(day?.total_minutes)}
                              {open && <span className="pill live" title="Checked in now">●</span>}
                            </>
                          )}
                        </td>
                      );
                    })}
                    <td className="num strong">{formatMinutes(r.total_minutes)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
