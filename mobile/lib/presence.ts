// Presence heartbeats: foreground helper plus the background location task.
//
// While checked in the app tells the server every `heartbeat_interval_seconds` where the
// phone is and which beacons it hears; the server decides present / away / outside.
//
// Background behaviour
// - Android: expo-location runs a foreground service (persistent notification
//   "Attendify is tracking your work session") so the process stays alive. Each location
//   update triggers a heartbeat, preceded by a short BLE scan for the beacons.
// - iOS: the "location" background mode keeps the app running while location updates are
//   active, so heartbeats are sent, but iBeacon advertisements are not visible to
//   CoreBluetooth (see lib/ble.ts). The iOS-native path is CoreLocation iBeacon region
//   monitoring/ranging, which wakes the app on region entry/exit; that needs a native
//   module and is not implemented here. Background iOS heartbeats therefore carry no
//   beacon readings.
import * as Location from "expo-location";
import * as SecureStore from "expo-secure-store";
import * as TaskManager from "expo-task-manager";
import { DESK_DEVICES_KEY, HEARTBEAT_INTERVAL_KEY, isNetworkError, loadJson, newIdempotencyKey } from "./api";
import {
  buildHeartbeatBody,
  fetchStatus,
  HeartbeatResponse,
  PresenceState,
  sendHeartbeat as postHeartbeat,
  SessionOut,
} from "./attendanceApi";
import { scanOnce } from "./ble";
import { Beacon, BeaconReading } from "./beacons";
import { collectTrust } from "./collectTrust";
import { PositionLike } from "./trust";

export type { HeartbeatResponse, PresenceState, SessionOut };

export const BACKGROUND_TASK = "attendify-presence-heartbeat";
export const DEFAULT_HEARTBEAT_SECONDS = 300;

/** When the last heartbeat was sent from this JS runtime (foreground loop and task share it). */
let lastHeartbeatAt = 0;

/** True when enough of the interval has passed since the last heartbeat (20% slack). */
export function shouldSendHeartbeat(now: number, last: number, intervalSeconds: number): boolean {
  return now - last >= intervalSeconds * 1000 * 0.8;
}

/**
 * One heartbeat attempt: trust evidence (location with the mock flag, device with root and
 * integrity) plus beacon readings. The Idempotency-Key is created per attempt and reused by the
 * HTTP client when it retries after a network error or 5xx.
 */
export async function sendHeartbeat(position: PositionLike, beacons: BeaconReading[]): Promise<HeartbeatResponse> {
  const trust = await collectTrust(position);
  const response = await postHeartbeat(buildHeartbeatBody({ trust, beacons }), newIdempotencyKey());
  lastHeartbeatAt = Date.now();
  return response;
}

export function markHeartbeatSent() {
  lastHeartbeatAt = Date.now();
}

async function storedInterval(): Promise<number> {
  const value = Number(await SecureStore.getItemAsync(HEARTBEAT_INTERVAL_KEY));
  return value > 0 ? value : DEFAULT_HEARTBEAT_SECONDS;
}

// The task body. It must be defined at module scope so it is registered before the
// app mounts (imported from app/_layout.tsx), including when Android starts the
// process headless for a location update.
TaskManager.defineTask<{ locations?: Location.LocationObject[] }>(BACKGROUND_TASK, async ({ data, error }) => {
  if (error) return;
  const location = data?.locations?.[data.locations.length - 1];
  if (!location) return;
  try {
    const interval = await storedInterval();
    // The foreground loop shares lastHeartbeatAt, so an active app is not heart-beaten twice.
    if (!shouldSendHeartbeat(Date.now(), lastHeartbeatAt, interval)) return;

    const devices = (await loadJson<Beacon[]>(DESK_DEVICES_KEY)) ?? [];
    const readings = devices.length ? await scanOnce(devices) : [];
    const result = await sendHeartbeat(location, readings);
    await SecureStore.setItemAsync(HEARTBEAT_INTERVAL_KEY, String(result.next_heartbeat_seconds || interval));
    if (!result.checked_in) await stopBackgroundTracking();
  } catch (error) {
    // Network problems: the next update retries. Anything else (409 not checked in, a lost
    // session) means there may be nothing to track any more.
    if (isNetworkError(error)) return;
    try {
      await stopIfSessionOver();
    } catch {
      /* ignore */
    }
  }
});

async function stopIfSessionOver() {
  const status = await fetchStatus();
  if (!status?.session) await stopBackgroundTracking();
}

export type LocationPermissionState = { foreground: boolean; background: boolean };

export async function getLocationPermissionState(): Promise<LocationPermissionState> {
  const [fg, bg] = await Promise.all([
    Location.getForegroundPermissionsAsync(),
    Location.getBackgroundPermissionsAsync(),
  ]);
  return { foreground: fg.status === "granted", background: bg.status === "granted" };
}

export async function requestForegroundLocation(): Promise<boolean> {
  const { status } = await Location.requestForegroundPermissionsAsync();
  return status === "granted";
}

/**
 * Background permission may only be requested after foreground permission was granted
 * (Android 11+ sends the user to system settings for it). Callers show an explanation
 * screen first.
 */
export async function requestBackgroundLocation(): Promise<boolean> {
  const fg = await Location.getForegroundPermissionsAsync();
  if (fg.status !== "granted") return false;
  const { status } = await Location.requestBackgroundPermissionsAsync();
  return status === "granted";
}

export async function startBackgroundTracking(intervalSeconds: number): Promise<boolean> {
  const permissions = await getLocationPermissionState();
  if (!permissions.foreground || !permissions.background) return false;
  await SecureStore.setItemAsync(HEARTBEAT_INTERVAL_KEY, String(intervalSeconds));
  if (await Location.hasStartedLocationUpdatesAsync(BACKGROUND_TASK)) return true;
  await Location.startLocationUpdatesAsync(BACKGROUND_TASK, {
    accuracy: Location.Accuracy.Balanced,
    // Android honours timeInterval; iOS ignores it and uses distanceInterval/significant
    // movement, which is why the task also checks shouldSendHeartbeat().
    timeInterval: intervalSeconds * 1000,
    distanceInterval: 0,
    pausesUpdatesAutomatically: false,
    showsBackgroundLocationIndicator: true,
    activityType: Location.ActivityType.Other,
    foregroundService: {
      notificationTitle: "Attendify",
      notificationBody: "Attendify is tracking your work session",
      killServiceOnDestroy: false,
    },
  });
  return true;
}

export async function stopBackgroundTracking(): Promise<void> {
  try {
    if (await Location.hasStartedLocationUpdatesAsync(BACKGROUND_TASK)) {
      await Location.stopLocationUpdatesAsync(BACKGROUND_TASK);
    }
  } catch {
    /* task not registered (e.g. Expo Go / web) */
  }
}
