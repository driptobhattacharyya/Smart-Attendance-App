import { useEffect, useState } from "react";
import { api } from "../api.js";

const EMPTY = { name: "", latitude: "", longitude: "", radius: "150", timezone: "" };

export default function Offices() {
  const [offices, setOffices] = useState([]);
  const [form, setForm] = useState({
    ...EMPTY,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  });
  const [error, setError] = useState("");

  const load = () =>
    api("/offices")
      .then(setOffices)
      .catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  function useMyLocation() {
    navigator.geolocation?.getCurrentPosition(
      (pos) =>
        setForm((f) => ({
          ...f,
          latitude: pos.coords.latitude.toFixed(6),
          longitude: pos.coords.longitude.toFixed(6),
        })),
      () => setError("Could not read your location"),
    );
  }

  async function submit(e) {
    e.preventDefault();
    setError("");
    try {
      await api("/offices", {
        method: "POST",
        body: {
          name: form.name,
          latitude: Number(form.latitude),
          longitude: Number(form.longitude),
          radius: Number(form.radius),
          timezone: form.timezone,
        },
      });
      setForm({ ...EMPTY, timezone: form.timezone });
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  return (
    <section>
      <h2>Offices</h2>
      <p className="muted">
        Employees can only check in inside an office&apos;s radius. Pick coordinates from Google
        Maps (right-click → copy coordinates) or use your current location.
      </p>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th className="num">Latitude</th>
              <th className="num">Longitude</th>
              <th className="num">Radius</th>
              <th>Timezone</th>
            </tr>
          </thead>
          <tbody>
            {offices.map((o) => (
              <tr key={o.id}>
                <td>{o.name}</td>
                <td className="num">{o.latitude}</td>
                <td className="num">{o.longitude}</td>
                <td className="num">{o.radius} m</td>
                <td>{o.timezone}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <form className="card grid-form" onSubmit={submit}>
        <h3>Add office</h3>
        <label>
          Name
          <input value={form.name} onChange={set("name")} required />
        </label>
        <label>
          Latitude
          <input value={form.latitude} onChange={set("latitude")} inputMode="decimal" required />
        </label>
        <label>
          Longitude
          <input value={form.longitude} onChange={set("longitude")} inputMode="decimal" required />
        </label>
        <label>
          Radius (m)
          <input value={form.radius} onChange={set("radius")} inputMode="numeric" required />
        </label>
        <label>
          Timezone
          <input value={form.timezone} onChange={set("timezone")} required />
        </label>
        <div className="form-actions">
          <button type="button" className="secondary" onClick={useMyLocation}>
            Use my location
          </button>
          <button type="submit">Add office</button>
        </div>
      </form>
    </section>
  );
}
