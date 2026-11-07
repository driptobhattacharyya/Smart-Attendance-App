import {
  base64ToBytes,
  beaconStatus,
  bestReadings,
  latestForBeacon,
  matchesBeacon,
  parseIBeacon,
  Beacon,
  Sighting,
} from "../beacons";

const UUID = "e2c56db5-dffb-48d2-b060-d0f5a71096e0";

/** Build the manufacturer data react-native-ble-plx would give us (base64). */
function frame(uuid: string, major: number, minor: number, tx = -59, header = [0x4c, 0x00, 0x02, 0x15]) {
  const uuidBytes = uuid.replace(/-/g, "").match(/../g)!.map((h) => parseInt(h, 16));
  const bytes = [...header, ...uuidBytes, major >> 8, major & 0xff, minor >> 8, minor & 0xff, tx & 0xff];
  return Buffer.from(bytes).toString("base64");
}

const beacon = (over: Partial<Beacon> = {}): Beacon => ({
  id: "b1",
  name: "Desk 14",
  uuid: UUID,
  major: 1,
  minor: 2,
  ...over,
});

describe("base64ToBytes", () => {
  it("decodes with and without padding", () => {
    expect(base64ToBytes("AQID")).toEqual([1, 2, 3]);
    expect(base64ToBytes("AQI=")).toEqual([1, 2]);
    expect(base64ToBytes("AQI")).toEqual([1, 2]);
  });
  it("rejects garbage", () => {
    expect(base64ToBytes("")).toBeNull();
    expect(base64ToBytes(null)).toBeNull();
    expect(base64ToBytes("!!!!")).toBeNull();
  });
});

describe("parseIBeacon", () => {
  it("parses uuid, major and minor", () => {
    expect(parseIBeacon(frame(UUID, 1, 2))).toEqual({ uuid: UUID, major: 1, minor: 2 });
  });

  it("reads major and minor as big endian 16-bit values", () => {
    expect(parseIBeacon(frame(UUID, 0xabcd, 0xffff))).toEqual({ uuid: UUID, major: 0xabcd, minor: 65535 });
    expect(parseIBeacon(frame(UUID, 0, 0))).toEqual({ uuid: UUID, major: 0, minor: 0 });
  });

  it("returns null for other manufacturers", () => {
    expect(parseIBeacon(frame(UUID, 1, 2, -59, [0x06, 0x00, 0x02, 0x15]))).toBeNull();
  });

  it("returns null for other Apple frame types", () => {
    expect(parseIBeacon(frame(UUID, 1, 2, -59, [0x4c, 0x00, 0x10, 0x05]))).toBeNull();
  });

  it("returns null for truncated or missing data", () => {
    const truncated = Buffer.from([0x4c, 0x00, 0x02, 0x15, 1, 2, 3]).toString("base64");
    expect(parseIBeacon(truncated)).toBeNull();
    expect(parseIBeacon(null)).toBeNull();
    expect(parseIBeacon(undefined)).toBeNull();
  });
});

describe("matchesBeacon", () => {
  const reading = { uuid: UUID, major: 1, minor: 2 };

  it("matches exact identifiers", () => {
    expect(matchesBeacon(reading, beacon())).toBe(true);
  });
  it("compares the uuid case-insensitively", () => {
    expect(matchesBeacon({ ...reading, uuid: UUID.toUpperCase() }, beacon())).toBe(true);
    expect(matchesBeacon(reading, beacon({ uuid: UUID.toUpperCase() }))).toBe(true);
  });
  it("treats null major/minor as wildcards", () => {
    expect(matchesBeacon(reading, beacon({ major: null, minor: null }))).toBe(true);
    expect(matchesBeacon(reading, beacon({ minor: null }))).toBe(true);
    expect(matchesBeacon(reading, beacon({ major: null }))).toBe(true);
  });
  it("rejects different uuid, major or minor", () => {
    expect(matchesBeacon(reading, beacon({ uuid: "00000000-0000-0000-0000-000000000000" }))).toBe(false);
    expect(matchesBeacon(reading, beacon({ major: 9 }))).toBe(false);
    expect(matchesBeacon(reading, beacon({ minor: 9 }))).toBe(false);
  });
  it("does not confuse 0 with a wildcard", () => {
    expect(matchesBeacon({ ...reading, major: 5 }, beacon({ major: 0 }))).toBe(false);
    expect(matchesBeacon({ ...reading, major: 0 }, beacon({ major: 0 }))).toBe(true);
  });
});

describe("bestReadings", () => {
  const now = 100_000;
  const s = (rssi: number, ageMs: number, minor = 2): Sighting => ({
    uuid: UUID.toUpperCase(),
    major: 1,
    minor,
    rssi,
    seenAt: now - ageMs,
  });

  it("keeps the strongest reading per beacon within the window", () => {
    const out = bestReadings([s(-80, 1000), s(-60, 5000), s(-70, 2000), s(-40, 31_000)], now);
    expect(out).toEqual([{ uuid: UUID, major: 1, minor: 2, rssi: -60 }]);
  });
  it("keeps different beacons apart", () => {
    const out = bestReadings([s(-80, 1000, 2), s(-50, 1000, 3)], now);
    expect(out).toHaveLength(2);
  });
  it("is empty when nothing is recent", () => {
    expect(bestReadings([s(-50, 60_000)], now)).toEqual([]);
  });
});

describe("beaconStatus / latestForBeacon", () => {
  const now = 50_000;
  const sighting = (rssi: number, ageMs: number): Sighting => ({ uuid: UUID, major: 1, minor: 2, rssi, seenAt: now - ageMs });

  it("is near at or above the threshold, far below it", () => {
    expect(beaconStatus({ rssi: -80 })).toBe("near");
    expect(beaconStatus({ rssi: -81 })).toBe("far");
    expect(beaconStatus({ rssi: -70 }, -65)).toBe("far");
  });
  it("is not seen without a sighting", () => {
    expect(beaconStatus(null)).toBe("not seen");
  });
  it("finds the latest matching recent sighting", () => {
    const found = latestForBeacon(beacon(), [sighting(-90, 8000), sighting(-60, 1000)], now);
    expect(found?.rssi).toBe(-60);
    expect(latestForBeacon(beacon(), [sighting(-60, 40_000)], now)).toBeNull();
    expect(latestForBeacon(beacon({ major: 7 }), [sighting(-60, 1000)], now)).toBeNull();
  });
});
