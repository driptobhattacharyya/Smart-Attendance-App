// Pure iBeacon helpers: parsing advertisements, matching against the beacons the
// server assigned to me, and turning raw sightings into API readings.
// Nothing here touches native modules so it can be unit tested.

/**
 * A thing the phone should scan for: a desk tag (exact uuid/major/minor) or a desk app
 * (org desk uuid with major/minor = null, because its identity rotates every 5 minutes).
 * Normalised from GET /places/desk-devices/mine by normalizeDeskDevice().
 */
export type Beacon = {
  id: string;
  kind?: "tag" | "desk_app";
  office_id?: string | null;
  name: string;
  /** Proximity UUID. */
  uuid: string;
  /** null = match any (rotating desk apps) */
  major: number | null;
  /** null = match any (rotating desk apps) */
  minor: number | null;
  assigned_user_id?: string | null;
  active?: boolean;
};

/** The wire shape of a desk device (api-v1 "Places"). */
export type DeskDeviceDto = {
  id: string;
  kind?: "tag" | "desk_app";
  name: string;
  office_id?: string | null;
  assigned_user_id?: string | null;
  active?: boolean;
  ibeacon_uuid?: string | null;
  uuid?: string | null;
  major?: number | null;
  minor?: number | null;
};

/** Desk devices without an iBeacon UUID cannot be scanned for and are dropped. */
export function normalizeDeskDevices(devices: DeskDeviceDto[] | null | undefined): Beacon[] {
  const out: Beacon[] = [];
  for (const d of devices ?? []) {
    const uuid = d.ibeacon_uuid ?? d.uuid;
    if (!uuid || d.active === false) continue;
    out.push({
      id: d.id,
      kind: d.kind,
      name: d.name,
      office_id: d.office_id,
      assigned_user_id: d.assigned_user_id,
      active: d.active,
      uuid: uuid.toLowerCase(),
      major: d.major ?? null,
      minor: d.minor ?? null,
    });
  }
  return out;
}

/** What the phone sends to the server (check-in and heartbeat body). */
export type BeaconReading = {
  uuid: string;
  major: number | null;
  minor: number | null;
  rssi: number;
};

export type ParsedIBeacon = { uuid: string; major: number; minor: number };

/** A reading plus when we heard it (ms since epoch). */
export type Sighting = BeaconReading & { seenAt: number };

export type BeaconStatus = "near" | "far" | "not seen";

/** Server default for BLUETOOTH_MIN_RSSI. */
export const DEFAULT_MIN_RSSI = -80;
/** Readings older than this are not sent to the server. */
export const READING_WINDOW_MS = 30_000;
/** A beacon not heard for this long is shown as "not seen". */
export const NOT_SEEN_AFTER_MS = 15_000;

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** Decode base64 into bytes. Returns null for malformed input. (Hermes has no Buffer.) */
export function base64ToBytes(input: string | null | undefined): number[] | null {
  if (typeof input !== "string") return null;
  const clean = input.replace(/=+$/, "");
  if (!clean || /[^A-Za-z0-9+/]/.test(clean) || clean.length % 4 === 1) return null;
  const out: number[] = [];
  let bits = 0;
  let acc = 0;
  for (const ch of clean) {
    acc = (acc << 6) | B64.indexOf(ch);
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out.push((acc >> bits) & 0xff);
    }
  }
  return out;
}

const hex = (bytes: number[]) => bytes.map((b) => b.toString(16).padStart(2, "0")).join("");

/** Format 16 bytes as a lowercase dashed UUID. */
export function bytesToUuid(bytes: number[]): string {
  const h = hex(bytes);
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20, 32)}`;
}

/**
 * Parse the manufacturer data of a BLE advertisement (base64, as react-native-ble-plx
 * returns it) as an iBeacon frame:
 *   4C 00 | 02 15 | 16-byte UUID | major (2, big endian) | minor (2, big endian) | tx power (1)
 * Returns null if it is not an iBeacon.
 */
export function parseIBeacon(manufacturerData: string | null | undefined): ParsedIBeacon | null {
  const b = base64ToBytes(manufacturerData);
  if (!b || b.length < 25) return null;
  // Apple company id 0x004C, little endian on the wire.
  if (b[0] !== 0x4c || b[1] !== 0x00) return null;
  // iBeacon type 0x02, length 0x15 (21).
  if (b[2] !== 0x02 || b[3] !== 0x15) return null;
  return {
    uuid: bytesToUuid(b.slice(4, 20)),
    major: (b[20] << 8) | b[21],
    minor: (b[22] << 8) | b[23],
  };
}

/** Normalise a UUID for comparison (case, dashes, braces). */
export const normalizeUuid = (uuid: string) => uuid.toLowerCase().replace(/[^0-9a-f]/g, "");

/**
 * A reading matches a beacon when the UUID is equal (case-insensitive) and
 * major/minor are equal or the beacon's value is null (wildcard).
 */
export function matchesBeacon(
  reading: Pick<BeaconReading, "uuid" | "major" | "minor">,
  beacon: Pick<Beacon, "uuid" | "major" | "minor">
): boolean {
  if (normalizeUuid(reading.uuid) !== normalizeUuid(beacon.uuid)) return false;
  if (beacon.major !== null && beacon.major !== undefined && beacon.major !== reading.major) return false;
  if (beacon.minor !== null && beacon.minor !== undefined && beacon.minor !== reading.minor) return false;
  return true;
}

export const sightingKey = (r: Pick<BeaconReading, "uuid" | "major" | "minor">) =>
  `${normalizeUuid(r.uuid)}|${r.major ?? "*"}|${r.minor ?? "*"}`;

/** Keep only the best (highest) RSSI per distinct beacon among readings from the last `windowMs`. */
export function bestReadings(
  sightings: Sighting[],
  now: number,
  windowMs: number = READING_WINDOW_MS
): BeaconReading[] {
  const best = new Map<string, BeaconReading>();
  for (const s of sightings) {
    if (now - s.seenAt > windowMs) continue;
    const key = sightingKey(s);
    const current = best.get(key);
    if (!current || s.rssi > current.rssi) {
      best.set(key, { uuid: s.uuid.toLowerCase(), major: s.major, minor: s.minor, rssi: s.rssi });
    }
  }
  return [...best.values()];
}

/** Best recent sighting that matches a beacon, or null. */
export function latestForBeacon(
  beacon: Beacon,
  sightings: Sighting[],
  now: number,
  windowMs: number = NOT_SEEN_AFTER_MS
): Sighting | null {
  let best: Sighting | null = null;
  for (const s of sightings) {
    if (now - s.seenAt > windowMs) continue;
    if (!matchesBeacon(s, beacon)) continue;
    if (!best || s.seenAt > best.seenAt) best = s;
  }
  return best;
}

/** near = heard with RSSI >= minRssi, far = heard but weaker, not seen = no recent reading. */
export function beaconStatus(
  sighting: Pick<Sighting, "rssi"> | null,
  minRssi: number = DEFAULT_MIN_RSSI
): BeaconStatus {
  if (!sighting) return "not seen";
  return sighting.rssi >= minRssi ? "near" : "far";
}

/** Drop sightings that are too old to matter. */
export function pruneSightings(sightings: Sighting[], now: number, windowMs: number = READING_WINDOW_MS): Sighting[] {
  return sightings.filter((s) => now - s.seenAt <= windowMs);
}

/**
 * Readings to send to the server: the best recent sighting of every distinct beacon identity
 * that matches one of my desk devices. A device with null major/minor is a wildcard, so every
 * rotating (major, minor) heard with its UUID is forwarded and the server does the matching
 * against the identities of the current time windows. Everything else heard nearby is dropped.
 */
export function readingsForDevices(
  sightings: Sighting[],
  devices: Pick<Beacon, "uuid" | "major" | "minor">[],
  now: number,
  windowMs: number = READING_WINDOW_MS
): BeaconReading[] {
  if (devices.length === 0) return [];
  const relevant = sightings.filter((s) => devices.some((d) => matchesBeacon(s, d)));
  return bestReadings(relevant, now, windowMs);
}
