// Trust signals (architecture section 8). The phone sends evidence with check-in and every
// heartbeat; the SERVER decides what to do with it. Nothing here blocks the user.
//
//   location {lat, lon, accuracy_m, mock, captured_at}
//   device   {platform, app_version, rooted, integrity_token?}
//
// Pure builders only: the native checks live in lib/deviceIntegrity.ts.

export type LocationEvidence = {
  lat: number;
  lon: number;
  accuracy_m: number | null;
  /** Android: the position came from a mock location provider (expo-location `mocked`). */
  mock: boolean;
  /** When the fix was taken (ISO-8601 UTC), not when it was sent: the server rejects stale fixes. */
  captured_at: string;
};

export type DeviceEvidence = {
  platform: "android" | "ios" | string;
  app_version: string;
  rooted: boolean;
  integrity_token?: string;
};

export type TrustPayload = { location: LocationEvidence; device: DeviceEvidence };

/** The parts of expo-location's LocationObject that we use. */
export type PositionLike = {
  coords: { latitude: number; longitude: number; accuracy?: number | null };
  timestamp: number;
  mocked?: boolean;
};

export function buildLocationEvidence(position: PositionLike, platform: string = "android"): LocationEvidence {
  const accuracy = position.coords.accuracy;
  return {
    lat: position.coords.latitude,
    lon: position.coords.longitude,
    accuracy_m: typeof accuracy === "number" && Number.isFinite(accuracy) ? accuracy : null,
    // `mocked` only exists on Android; elsewhere there is no signal, so report false.
    mock: platform === "android" ? position.mocked === true : false,
    captured_at: new Date(position.timestamp).toISOString(),
  };
}

export function buildDeviceEvidence(input: {
  platform: string;
  appVersion: string;
  rooted: boolean;
  integrityToken?: string | null;
}): DeviceEvidence {
  const device: DeviceEvidence = {
    platform: input.platform,
    app_version: input.appVersion,
    rooted: input.rooted,
  };
  if (input.integrityToken) device.integrity_token = input.integrityToken;
  return device;
}

export const buildTrustPayload = (location: LocationEvidence, device: DeviceEvidence): TrustPayload => ({
  location,
  device,
});
