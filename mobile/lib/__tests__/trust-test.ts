jest.mock("../api", () => ({ apiFetch: jest.fn() }));

import { buildCheckInBody, buildCheckOutBody, buildHeartbeatBody } from "../attendanceApi";
import { buildDeviceEvidence, buildLocationEvidence, buildTrustPayload } from "../trust";

const position = (over: Record<string, unknown> = {}) => ({
  coords: { latitude: 12.97, longitude: 77.59, accuracy: 8.5 },
  timestamp: Date.UTC(2026, 10, 10, 9, 30, 15),
  ...over,
});

describe("location evidence", () => {
  it("builds {lat, lon, accuracy_m, mock, captured_at} from the fix time, not the send time", () => {
    expect(buildLocationEvidence(position(), "android")).toEqual({
      lat: 12.97,
      lon: 77.59,
      accuracy_m: 8.5,
      mock: false,
      captured_at: "2026-11-10T09:30:15.000Z",
    });
  });

  it("forwards expo-location's mocked flag on Android", () => {
    expect(buildLocationEvidence(position({ mocked: true }), "android").mock).toBe(true);
    expect(buildLocationEvidence(position({ mocked: false }), "android").mock).toBe(false);
  });

  it("reports mock=false when the platform gives no signal", () => {
    expect(buildLocationEvidence(position({ mocked: true }), "ios").mock).toBe(false);
  });

  it("uses null for a missing accuracy", () => {
    const p = position();
    p.coords.accuracy = null as any;
    expect(buildLocationEvidence(p).accuracy_m).toBeNull();
  });
});

describe("device evidence", () => {
  it("includes the integrity token only when there is one", () => {
    expect(buildDeviceEvidence({ platform: "android", appVersion: "1.2.3", rooted: false })).toEqual({
      platform: "android",
      app_version: "1.2.3",
      rooted: false,
    });
    expect(
      buildDeviceEvidence({ platform: "android", appVersion: "1.2.3", rooted: true, integrityToken: "tok" })
    ).toEqual({ platform: "android", app_version: "1.2.3", rooted: true, integrity_token: "tok" });
  });
});

describe("request bodies carry the trust payload", () => {
  const trust = buildTrustPayload(
    buildLocationEvidence(position({ mocked: true }), "android"),
    buildDeviceEvidence({ platform: "android", appVersion: "1.0.0", rooted: true, integrityToken: "tok" })
  );
  const beacons = [{ uuid: "e2c56db5-dffb-48d2-b060-d0f5a71096e0", major: 5, minor: 6, rssi: -60 }];

  it("check-in", () => {
    const body = buildCheckInBody({ trust, beacons });
    expect(body.mode).toBe("onsite");
    expect(body.location.mock).toBe(true);
    expect(body.device).toMatchObject({ rooted: true, integrity_token: "tok" });
    expect(body.beacons).toEqual(beacons);
  });

  it("remote check-in sends no beacons", () => {
    expect(buildCheckInBody({ trust, beacons, mode: "remote" })).toMatchObject({ mode: "remote", beacons: [] });
  });

  it("heartbeat", () => {
    const body = buildHeartbeatBody({ trust, beacons });
    expect(Object.keys(body).sort()).toEqual(["beacons", "device", "location"]);
  });

  it("check-out may omit evidence", () => {
    expect(buildCheckOutBody(null)).toEqual({});
    expect(buildCheckOutBody(trust)).toHaveProperty("location");
  });
});
