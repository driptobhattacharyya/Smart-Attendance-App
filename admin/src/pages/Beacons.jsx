import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const EMPTY = {
  name: "",
  office_id: "",
  uuid: "",
  major: "",
  minor: "",
  assigned_employee_id: "",
  active: true,
};

function shortUuid(uuid) {
  return `${uuid.slice(0, 8)}…${uuid.slice(-4)}`;
}

function isValidOptionalInt(value) {
  const v = String(value).trim();
  if (v === "") return true;
  if (!/^\d+$/.test(v)) return false;
  return Number(v) <= 65535;
}

function toOptionalInt(value) {
  const v = String(value).trim();
  return v === "" ? null : Number(v);
}

export default function Beacons({ me }) {
  const isAdmin = me.role === "admin";
  const [beacons, setBeacons] = useState([]);
  const [offices, setOffices] = useState([]);
  const [people, setPeople] = useState([]);
  const [officeFilter, setOfficeFilter] = useState("");
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");

  const load = useCallback(() => {
    setError("");
    const q = officeFilter ? `?office_id=${encodeURIComponent(officeFilter)}` : "";
    return api(`/beacons${q}`)
      .then(setBeacons)
      .catch((e) => setError(e.message));
  }, [officeFilter]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    api("/offices")
      .then(setOffices)
      .catch((e) => setError(e.message));
    api(isAdmin ? "/admin/users" : "/manager/team")
      .then(setPeople)
      .catch((e) => setError(e.message));
  }, [isAdmin]);

  const officeName = (id) => offices.find((o) => o.id === id)?.name || "—";
  const personById = useMemo(
    () => Object.fromEntries(people.map((p) => [p.employee_id, p])),
    [people],
  );

  // Active people in an office, plus the current assignee even if they are disabled
  // so an existing assignment still shows up in the select.
  const candidatesFor = (officeId, currentId = "") =>
    people.filter(
      (p) => p.office_id === officeId && (p.active || p.employee_id === currentId),
    );

  const setField = (key) => (e) => {
    const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [key]: value }));
  };

  function setOffice(e) {
    const officeId = e.target.value;
    setForm((f) => {
      const stillValid = candidatesFor(officeId).some(
        (p) => p.employee_id === f.assigned_employee_id,
      );
      return {
        ...f,
        office_id: officeId,
        assigned_employee_id: stillValid ? f.assigned_employee_id : "",
      };
    });
  }

  async function submit(e) {
    e.preventDefault();
    setFormError("");
    const uuid = form.uuid.trim().toLowerCase();
    if (!UUID_RE.test(uuid)) {
      setFormError(
        "UUID must be 8-4-4-4-12 hex digits, for example 2f234454-cf6d-4a0f-adf2-f4911ba9ffa6.",
      );
      return;
    }
    if (!isValidOptionalInt(form.major) || !isValidOptionalInt(form.minor)) {
      setFormError("Major and minor must be whole numbers from 0 to 65535, or left empty.");
      return;
    }
    try {
      await api("/beacons", {
        method: "POST",
        body: {
          name: form.name.trim(),
          office_id: form.office_id,
          uuid,
          major: toOptionalInt(form.major),
          minor: toOptionalInt(form.minor),
          assigned_employee_id: form.assigned_employee_id || null,
          active: form.active,
        },
      });
      setForm({ ...EMPTY, office_id: form.office_id });
      load();
    } catch (err) {
      setFormError(err.message);
    }
  }

  async function update(beacon, changes) {
    setError("");
    try {
      await api(`/beacons/${beacon.id}`, { method: "PATCH", body: changes });
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  async function remove(beacon) {
    if (!confirm(`Delete beacon "${beacon.name}"? Phones will stop detecting it.`)) return;
    setError("");
    try {
      await api(`/beacons/${beacon.id}`, { method: "DELETE" });
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <section>
      <div className="toolbar">
        <h2>Beacons</h2>
        <label className="inline">
          Office
          <select value={officeFilter} onChange={(e) => setOfficeFilter(e.target.value)}>
            <option value="">All offices</option>
            {offices.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="muted">
        Stick one beacon on each desk and assign it to that person, or add shared beacons for a
        whole office. Any iBeacon-compatible beacon works.
      </p>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Office</th>
              <th>UUID</th>
              <th className="num">Major</th>
              <th className="num">Minor</th>
              <th>Assigned to</th>
              <th>Active</th>
              {isAdmin && <th />}
            </tr>
          </thead>
          <tbody>
            {beacons.length === 0 && (
              <tr>
                <td colSpan={isAdmin ? 8 : 7} className="muted">
                  No beacons yet.
                </td>
              </tr>
            )}
            {beacons.map((b) => {
              const person = b.assigned_employee_id ? personById[b.assigned_employee_id] : null;
              return (
                <tr key={b.id} className={b.active ? "" : "inactive"}>
                  <td>{b.name}</td>
                  <td>{officeName(b.office_id)}</td>
                  <td>
                    <span className="mono" title={b.uuid}>
                      {shortUuid(b.uuid)}
                    </span>
                  </td>
                  <td className="num">{b.major ?? <span className="muted">any</span>}</td>
                  <td className="num">{b.minor ?? <span className="muted">any</span>}</td>
                  <td>
                    {isAdmin ? (
                      <select
                        aria-label={`Assigned employee for ${b.name}`}
                        value={b.assigned_employee_id || ""}
                        onChange={(e) =>
                          update(b, { assigned_employee_id: e.target.value || null })
                        }
                      >
                        <option value="">Shared (whole office)</option>
                        {candidatesFor(b.office_id, b.assigned_employee_id).map((p) => (
                          <option key={p.employee_id} value={p.employee_id}>
                            {p.name} ({p.employee_id})
                          </option>
                        ))}
                      </select>
                    ) : b.assigned_employee_id ? (
                      <>
                        {person?.name || "Unknown employee"}
                        <div className="muted small">{b.assigned_employee_id}</div>
                      </>
                    ) : (
                      <span className="muted">Shared (whole office)</span>
                    )}
                  </td>
                  <td>
                    {isAdmin ? (
                      <input
                        type="checkbox"
                        aria-label={`Active: ${b.name}`}
                        checked={b.active}
                        onChange={(e) => update(b, { active: e.target.checked })}
                      />
                    ) : b.active ? (
                      "Active"
                    ) : (
                      <span className="muted">Disabled</span>
                    )}
                  </td>
                  {isAdmin && (
                    <td className="actions">
                      <button className="secondary" onClick={() => remove(b)}>
                        Delete
                      </button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {isAdmin && (
        <form className="card grid-form" onSubmit={submit}>
          <h3>Add beacon</h3>
          {formError && <p className="error">{formError}</p>}
          <label>
            Name
            <input value={form.name} onChange={setField("name")} required />
          </label>
          <label>
            Office
            <select value={form.office_id} onChange={setOffice} required>
              <option value="" disabled>
                Choose an office
              </option>
              {offices.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            UUID
            <input
              className="mono"
              value={form.uuid}
              onChange={setField("uuid")}
              spellCheck={false}
              placeholder="2f234454-cf6d-4a0f-adf2-f4911ba9ffa6"
              required
            />
          </label>
          <label>
            Major (optional)
            <input value={form.major} onChange={setField("major")} inputMode="numeric" />
          </label>
          <label>
            Minor (optional)
            <input value={form.minor} onChange={setField("minor")} inputMode="numeric" />
          </label>
          <label>
            Assigned employee
            <select
              value={form.assigned_employee_id}
              onChange={setField("assigned_employee_id")}
              disabled={!form.office_id}
            >
              <option value="">Shared (whole office)</option>
              {candidatesFor(form.office_id).map((p) => (
                <option key={p.employee_id} value={p.employee_id}>
                  {p.name} ({p.employee_id})
                </option>
              ))}
            </select>
          </label>
          <label className="inline">
            <input type="checkbox" checked={form.active} onChange={setField("active")} />
            Active
          </label>
          <div className="form-actions">
            <button type="submit">Add beacon</button>
          </div>
        </form>
      )}
    </section>
  );
}
