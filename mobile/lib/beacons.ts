// Pure iBeacon helpers: parsing advertisements, matching against the beacons the
// server assigned to me, and turning raw sightings into API readings.
// Nothing here touches native modules so it can be unit tested.

/** A beacon registered by an admin (GET /beacons/mine). */
export type Beacon = {
  id: string;
  office_id?: string;
  name: string;
  /** Proximity UUID, lowercase with dashes. */
  uuid: string;
  /** null = match any */
  major: number | null;
  /** null = match any */
  minor: number | null;
  assigned_employee_id?: string | null;
  active?: boolean;
};

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
