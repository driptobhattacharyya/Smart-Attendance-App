import {
  base64ToBytes,
  beaconStatus,
  bestReadings,
  latestForBeacon,
  matchesBeacon,
  normalizeDeskDevices,
  readingsForDevices,
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

describe("desk devices (rotating identities)", () => {
  const DESK_UUID = "11111111-2222-3333-4444-555555555555";
  const desk = beacon({ id: "d1", kind: "desk_app", uuid: DESK_UUID, major: null, minor: null });
  const tag = beacon({ id: "t1", kind: "tag", major: 1, minor: 2 });
  const heard = (uuid: string, major: number, minor: number, rssi: number, seenAt: number): Sighting => ({
    uuid,
    major,
    minor,
    rssi,
    seenAt,
  });

  it("treats null major/minor as a wildcard for any rotating value", () => {
    expect(matchesBeacon({ uuid: DESK_UUID, major: 40716, minor: 55737 }, desk)).toBe(true);
    expect(matchesBeacon({ uuid: DESK_UUID.toUpperCase(), major: 1, minor: 2 }, desk)).toBe(true);
    expect(matchesBeacon({ uuid: UUID, major: 40716, minor: 55737 }, desk)).toBe(false);
  });

  it("a wildcard on one side still checks the other", () => {
    const half = beacon({ major: 7, minor: null });
    expect(matchesBeacon({ uuid: UUID, major: 7, minor: 9 }, half)).toBe(true);
    expect(matchesBeacon({ uuid: UUID, major: 8, minor: 9 }, half)).toBe(false);
  });

  it("forwards every reading whose UUID matches a desk device, whatever its major/minor", () => {
    const now = 100_000;
    const sightings = [
      heard(DESK_UUID, 40716, 55737, -70, now - 1000), // current window
      heard(DESK_UUID, 50226, 57433, -75, now - 2000), // neighbouring window
      heard(DESK_UUID, 40716, 55737, -60, now - 500), // same identity, stronger
      heard(UUID, 1, 2, -50, now - 500), // my tag
      heard(UUID, 9, 9, -50, now - 500), // someone else's tag: same UUID, other major/minor
      heard("99999999-0000-0000-0000-000000000000", 1, 1, -40, now - 500), // unrelated beacon
    ];
    const readings = readingsForDevices(sightings, [desk, tag], now);
    expect(readings).toHaveLength(3);
    expect(readings).toEqual(
      expect.arrayContaining([
        { uuid: DESK_UUID, major: 40716, minor: 55737, rssi: -60 },
        { uuid: DESK_UUID, major: 50226, minor: 57433, rssi: -75 },
        { uuid: UUID, major: 1, minor: 2, rssi: -50 },
      ])
    );
  });

  it("sends nothing when there are no devices or the readings are old", () => {
    expect(readingsForDevices([heard(DESK_UUID, 1, 1, -50, 0)], [], 1000)).toEqual([]);
    expect(readingsForDevices([heard(DESK_UUID, 1, 1, -50, 0)], [desk], 1_000_000)).toEqual([]);
  });

  it("normalises GET /places/desk-devices/mine", () => {
    const devices = normalizeDeskDevices([
      { id: "a", kind: "desk_app", name: "Dana's desk", ibeacon_uuid: DESK_UUID.toUpperCase(), major: null, minor: null },
      { id: "b", kind: "tag", name: "Tag", ibeacon_uuid: UUID, major: 3, minor: 4, active: true },
      { id: "c", kind: "tag", name: "Off", ibeacon_uuid: UUID, major: 3, minor: 4, active: false },
      { id: "d", kind: "desk_app", name: "No uuid", ibeacon_uuid: null },
    ]);
    expect(devices.map((d) => d.id)).toEqual(["a", "b"]);
    expect(devices[0]).toMatchObject({ uuid: DESK_UUID, major: null, minor: null });
    expect(devices[1]).toMatchObject({ major: 3, minor: 4 });
  });
});
