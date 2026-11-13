import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Circle, MapContainer, Marker, TileLayer, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import { Building2, Clock, MapPin, Pencil, Plus, Radius } from "lucide-react";
import { api } from "../api/client";
import type { Office } from "../api/types";
import { useOffices } from "../lib/queries";
import { useToast } from "../lib/toast";
import { Badge, Card, EmptyState, ErrorState, PageHeader, Skeleton } from "../ui/misc";
import { Button, IconButton } from "../ui/Button";
import { Field, Input, Select, Switch } from "../ui/form";
import { Dialog } from "../ui/Dialog";

const pin = L.divIcon({ className: "", html: '<div class="pin"></div>', iconSize: [28, 28], iconAnchor: [14, 28] });

function zones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf: (k: string) => string[] }).supportedValuesOf("timeZone");
  } catch {
    return ["UTC"];
  }
}

const hhmm = (t: string | null | undefined) => (t ? t.slice(0, 5) : "");

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return "UTC";
  }
}

export function Offices() {
  const q = useOffices();
  const [editing, setEditing] = useState<Office | "new" | null>(null);

  return (
    <>
      <PageHeader
        title="Offices"
        description="Each office has a geofence. Time only counts while a phone reports from inside the circle."
        actions={
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
            Add office
          </Button>
        }
      />
      {q.isLoading ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-36 rounded-xl" />
          <Skeleton className="h-36 rounded-xl" />
        </div>
      ) : q.isError ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => q.refetch()} feature="Offices" />
        </Card>
      ) : q.data?.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Building2 />}
            title="Add your first office"
            description="Drop a pin on the map and set a radius. People are assigned to an office when you add them."
            action={
              <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
                Add office
              </Button>
            }
          />
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {q.data?.map((o) => (
            <Card key={o.id} className="p-4 sm:p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="flex items-center gap-2 font-semibold">
                    <span className="truncate">{o.name}</span>
                    {!o.active && <Badge>Inactive</Badge>}
                  </h2>
                  <p className="truncate text-[13px] text-muted">{o.address || `${o.latitude.toFixed(4)}, ${o.longitude.toFixed(4)}`}</p>
                </div>
                <IconButton label={`Edit ${o.name}`} onClick={() => setEditing(o)}>
                  <Pencil className="size-4" />
                </IconButton>
              </div>
              <dl className="mt-4 grid grid-cols-2 gap-x-2 gap-y-3 text-[13px] sm:grid-cols-4">
                <div>
                  <dt className="flex items-center gap-1 text-xs text-muted">
                    <Radius className="size-3" /> Radius
                  </dt>
                  <dd className="font-medium tabular-nums">{o.radius_m} m</dd>
                </div>
                <div>
                  <dt className="flex items-center gap-1 text-xs text-muted">
                    <Clock className="size-3" /> Hours
                  </dt>
                  <dd className="font-medium tabular-nums">
                    {o.work_start && o.work_end ? `${hhmm(o.work_start)} – ${hhmm(o.work_end)}` : "Not set"}
                  </dd>
                </div>
                <div className="min-w-0">
                  <dt className="flex items-center gap-1 text-xs text-muted">
                    <MapPin className="size-3" /> Timezone
                  </dt>
                  <dd className="truncate font-medium" title={o.timezone}>
                    {o.timezone}
                  </dd>
                </div>
              </dl>
            </Card>
          ))}
        </div>
      )}
      {editing && <OfficeDialog office={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function ClickToPlace({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  useMapEvents({ click: (e) => onPick(e.latlng.lat, e.latlng.lng) });
  return null;
}

function Recenter({ lat, lng, radius }: { lat: number; lng: number; radius: number }) {
  const map = useMap();
  useEffect(() => {
    // The map mounts inside a freshly opened dialog, so measure it again once laid out.
    const t = setTimeout(() => {
      map.invalidateSize();
      map.fitBounds(L.latLng(lat, lng).toBounds(Math.max(radius * 4, 600)));
    }, 60);
    return () => clearTimeout(t);
    // Only on first mount; later moves must not yank the view around.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);
  return null;
}

function OfficeDialog({ office, onClose }: { office: Office | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(office?.name ?? "");
  const [address, setAddress] = useState(office?.address ?? "");
  const [lat, setLat] = useState(office?.latitude ?? 22.5726);
  const [lng, setLng] = useState(office?.longitude ?? 88.3639);
  const [radius, setRadius] = useState(office?.radius_m ?? 150);
  const [tz, setTz] = useState(office?.timezone ?? browserZone());
  const [start, setStart] = useState(hhmm(office?.work_start) || "09:30");
  const [end, setEnd] = useState(hhmm(office?.work_end) || "18:30");
  const [active, setActive] = useState(office?.active ?? true);
  const [placed, setPlaced] = useState(!!office);
  const tzList = useMemo(() => {
    const l = zones();
    return l.includes(tz) ? l : [tz, ...l];
  }, [tz]);

  const m = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        address: address.trim() || null,
        latitude: +lat.toFixed(6),
        longitude: +lng.toFixed(6),
        radius_m: radius,
        timezone: tz,
        work_start: start,
        work_end: end,
        active,
      };
      return office ? api<Office>(`/places/offices/${office.id}`, { method: "PATCH", body }) : api<Office>("/places/offices", { method: "POST", body });
    },
    onSuccess: () => {
      toast.success(office ? "Office saved" : "Office created");
      qc.invalidateQueries({ queryKey: ["offices"] });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const move = (la: number, ln: number) => {
    setLat(+la.toFixed(6));
    setLng(+ln.toFixed(6));
    setPlaced(true);
  };

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title={office ? `Edit ${office.name}` : "Add office"}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" form="office-form" variant="primary" loading={m.isPending} disabled={!placed}>
            {office ? "Save changes" : "Create office"}
          </Button>
        </>
      }
    >
      <form
        id="office-form"
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} autoFocus />}</Field>
          <Field label="Address (optional)">{(p) => <Input {...p} value={address} onChange={(e) => setAddress(e.target.value)} />}</Field>
        </div>

        <div>
          <p className="mb-1.5 text-[13px] font-medium">Location</p>
          <div className="overflow-hidden rounded-lg border border-line">
            <MapContainer center={[lat, lng]} zoom={16} scrollWheelZoom className="h-72 w-full sm:h-80">
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
                maxZoom={19}
              />
              <ClickToPlace onPick={move} />
              <Recenter lat={lat} lng={lng} radius={radius} />
              {placed && (
                <>
                  <Circle center={[lat, lng]} radius={radius} pathOptions={{ color: "#4f46e5", weight: 2, fillColor: "#6366f1", fillOpacity: 0.18 }} />
                  <Marker
                    position={[lat, lng]}
                    icon={pin}
                    draggable
                    eventHandlers={{
                      dragend: (e) => {
                        const p = (e.target as L.Marker).getLatLng();
                        move(p.lat, p.lng);
                      },
                    }}
                  />
                </>
              )}
            </MapContainer>
          </div>
          <p className="mt-1.5 text-xs text-muted">
            {placed ? "Drag the pin or click the map to move it." : "Click the map to place the office pin."}
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Latitude">
            {(p) => <Input {...p} type="number" step="any" min={-90} max={90} value={lat} onChange={(e) => e.target.value !== "" && move(+e.target.value, lng)} />}
          </Field>
          <Field label="Longitude">
            {(p) => <Input {...p} type="number" step="any" min={-180} max={180} value={lng} onChange={(e) => e.target.value !== "" && move(lat, +e.target.value)} />}
          </Field>
        </div>

        <Field label={`Geofence radius: ${radius} m`} hint="People must be inside this circle for time to count.">
          {(p) => (
            <input
              {...p}
              type="range"
              min={25}
              max={1000}
              step={5}
              value={Math.min(radius, 1000)}
              onChange={(e) => setRadius(+e.target.value)}
              className="w-full accent-[var(--accent)]"
            />
          )}
        </Field>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Timezone">
            {(p) => (
              <Select {...p} value={tz} onChange={(e) => setTz(e.target.value)}>
                {tzList.map((z) => (
                  <option key={z} value={z}>
                    {z}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Work starts" hint="Check-ins after this time count as late.">
            {(p) => <Input {...p} type="time" value={start} onChange={(e) => setStart(e.target.value)} required />}
          </Field>
          <Field label="Work ends">
            {(p) => <Input {...p} type="time" value={end} onChange={(e) => setEnd(e.target.value)} required />}
          </Field>
        </div>
        <Switch checked={active} onChange={setActive} label="Active" hint="Inactive offices cannot be used for check-in. Deactivate instead of deleting once people were assigned." />
      </form>
    </Dialog>
  );
}
