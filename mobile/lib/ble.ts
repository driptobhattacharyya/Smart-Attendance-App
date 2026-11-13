// Thin wrapper around react-native-ble-plx for scanning iBeacon advertisements.
//
// Platform notes:
// - Android: scanning works in the foreground and, while our location foreground service
//   is running, also in the background (see lib/presence.ts). Android 12+ needs the
//   BLUETOOTH_SCAN runtime permission; older versions need location.
// - iOS: CoreBluetooth hides the Apple manufacturer data of iBeacons from apps
//   (iBeacons are meant to be handled by CoreLocation), so on iOS this scan will usually
//   not see the beacons at all, in the foreground or background. The iOS-native route is
//   CoreLocation region monitoring/ranging (CLBeaconRegion), which expo-location does not
//   expose; it needs a small native module. Until then iOS devices cannot report beacon
//   readings and the server will treat them as "away" when Bluetooth is required.
import { PermissionsAndroid, Platform } from "react-native";
import { BleManager, ScanMode, State } from "react-native-ble-plx";
import { Beacon, BeaconReading, parseIBeacon, readingsForDevices, Sighting } from "./beacons";

let manager: BleManager | null = null;

function getManager(): BleManager {
  if (!manager) manager = new BleManager();
  return manager;
}

/** Ask for the runtime permissions needed to scan. Returns true when scanning is allowed. */
export async function requestBlePermissions(): Promise<boolean> {
  if (Platform.OS !== "android") return true; // iOS asks on first scan
  const apiLevel = typeof Platform.Version === "number" ? Platform.Version : parseInt(String(Platform.Version), 10);
  const wanted =
    apiLevel >= 31
      ? [PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN, PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT]
      : [PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION];
  const result = await PermissionsAndroid.requestMultiple(wanted);
  return wanted.every((p) => result[p] === PermissionsAndroid.RESULTS.GRANTED);
}

/** True when scan permissions are already granted (does not prompt). */
export async function hasBlePermissions(): Promise<boolean> {
  if (Platform.OS !== "android") return true;
  const apiLevel = typeof Platform.Version === "number" ? Platform.Version : parseInt(String(Platform.Version), 10);
  const wanted =
    apiLevel >= 31
      ? [PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN, PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT]
      : [PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION];
  const checks = await Promise.all(wanted.map((p) => PermissionsAndroid.check(p)));
  return checks.every(Boolean);
}

/** Resolves true when the Bluetooth radio is on (waits briefly while it initialises). */
export async function isBluetoothOn(timeoutMs = 3000): Promise<boolean> {
  const ble = getManager();
  const current = await ble.state();
  if (current === State.PoweredOn) return true;
  if (current === State.PoweredOff || current === State.Unauthorized || current === State.Unsupported) return false;
  return new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => {
      sub.remove();
      resolve(false);
    }, timeoutMs);
    const sub = ble.onStateChange((s) => {
      if (s === State.Resetting || s === State.Unknown) return;
      clearTimeout(timer);
      sub.remove();
      resolve(s === State.PoweredOn);
    }, true);
  });
}

/**
 * Start scanning and call `onSighting` for every iBeacon advertisement heard.
 * Returns a function that stops the scan.
 */
export function startBeaconScan(
  onSighting: (sighting: Sighting) => void,
  onError?: (message: string) => void
): () => void {
  const ble = getManager();
  let stopped = false;
  ble
    .startDeviceScan(null, { allowDuplicates: true, scanMode: ScanMode.LowLatency }, (error, device) => {
      if (stopped) return;
      if (error) {
        onError?.(error.message);
        return;
      }
      if (!device || device.rssi == null) return;
      const parsed = parseIBeacon(device.manufacturerData);
      if (parsed) onSighting({ ...parsed, rssi: device.rssi, seenAt: Date.now() });
    })
    .catch((e: Error) => onError?.(e.message));
  return () => {
    stopped = true;
    ble.stopDeviceScan().catch(() => {});
  };
}

/**
 * One-shot scan used by the background task: listen for `durationMs`, return the best
 * RSSI per beacon identity that matches one of my desk devices (null major/minor = wildcard,
 * for rotating desk apps). Returns [] on iOS (see the note at the top) or if scanning fails.
 */
export async function scanOnce(devices: Beacon[], durationMs = 4000): Promise<BeaconReading[]> {
  if (Platform.OS !== "android") return [];
  try {
    if (!(await hasBlePermissions()) || !(await isBluetoothOn())) return [];
    const heard: Sighting[] = [];
    const stop = startBeaconScan((s) => heard.push(s));
    await new Promise((resolve) => setTimeout(resolve, durationMs));
    stop();
    return readingsForDevices(heard, devices, Date.now(), durationMs + 1000);
  } catch {
    return [];
  }
}
