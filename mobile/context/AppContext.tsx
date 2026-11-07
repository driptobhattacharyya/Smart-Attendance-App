import React, { createContext, useCallback, useEffect, useMemo, useRef, useState, ReactNode } from "react";
import { Alert, AppState } from "react-native";
import { useRouter } from "expo-router";
import * as Location from "expo-location";
import * as SecureStore from "expo-secure-store";
import { apiFetch, BEACONS_KEY, clearSession, HEARTBEAT_INTERVAL_KEY, SECURE_BACKGROUND_OPTIONS } from "@/lib/api";
import { API_CONFIG } from "@/lib/config";
import { hasBlePermissions, isBluetoothOn, requestBlePermissions, startBeaconScan } from "@/lib/ble";
import {
  Beacon,
  beaconStatus,
  bestReadings,
  BeaconReading,
  latestForBeacon,
  pruneSightings,
  Sighting,
} from "@/lib/beacons";
import {
  DEFAULT_HEARTBEAT_SECONDS,
  getLocationPermissionState,
  LocationPermissionState,
  PresenceState,
  sendHeartbeat,
  SessionOut,
  startBackgroundTracking,
  stopBackgroundTracking,
} from "@/lib/presence";

export type Coords = { latitude: number; longitude: number; accuracy?: number | null };

export type Session = SessionOut;

export type BleStatus = "idle" | "scanning" | "no-permission" | "bluetooth-off" | "error";

interface AppContextType {
  isInsideGeofence: boolean;
  isFaceScanned: boolean;
  setIsFaceScanned: React.Dispatch<React.SetStateAction<boolean>>;
  /** Called by the location watcher with each new position. */
  handleEntry: (coords: Coords) => void;
  handleExit: (coords: Coords) => void;
  /** Server-side attendance state */
  isCheckedIn: boolean;
  openSession: Session | null;
  todayMinutes: number;
  /** present / away / outside as last decided by the server (null when not checked in). */
  presence: PresenceState | null;
  attendanceMessage: string | null;
  refreshStatus: () => Promise<void>;
  /** Beacons assigned to me (GET /beacons/mine). */
  beacons: Beacon[];
  /** Beacon advertisements heard in the last ~30 s. */
  sightings: Sighting[];
  bleStatus: BleStatus;
  bleError: string | null;
  /** Ask for Bluetooth permissions (if needed) and start scanning. */
  enableScanning: () => Promise<void>;
  /** True when any of my beacons is currently heard with enough signal. */
  isNearBeacon: boolean;
  locationPermissions: LocationPermissionState;
  refreshLocationPermissions: () => Promise<void>;
  isLoggedIn: boolean;
  setIsLoggedIn: React.Dispatch<React.SetStateAction<boolean>>;
  handleLogout: () => Promise<void>;
}

export const AppContext = createContext<AppContextType>(undefined as unknown as AppContextType);

const SIGHTINGS_FLUSH_MS = 1000;

export const AppProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const router = useRouter();
  const [isInsideGeofence, setIsInsideGeofence] = useState(false);
  const [isFaceScanned, setIsFaceScanned] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  const [openSession, setOpenSession] = useState<Session | null>(null);
  const [todayMinutes, setTodayMinutes] = useState(0);
  const [presence, setPresence] = useState<PresenceState | null>(null);
  const [attendanceMessage, setAttendanceMessage] = useState<string | null>(null);
  const [heartbeatSeconds, setHeartbeatSeconds] = useState(DEFAULT_HEARTBEAT_SECONDS);

  const [beacons, setBeacons] = useState<Beacon[]>([]);
  const [sightings, setSightings] = useState<Sighting[]>([]);
  const [bleStatus, setBleStatus] = useState<BleStatus>("idle");
  const [bleError, setBleError] = useState<string | null>(null);
  const [scanRequest, setScanRequest] = useState(0);
  const [locationPermissions, setLocationPermissions] = useState<LocationPermissionState>({
    foreground: false,
    background: false,
  });

  const lastCoords = useRef<Coords | null>(null);
  const busy = useRef(false);
  const sightingsRef = useRef<Sighting[]>([]);
  const heartbeatSecondsRef = useRef(heartbeatSeconds);
  heartbeatSecondsRef.current = heartbeatSeconds;

  // ---- attendance status -------------------------------------------------

  const refreshStatus = useCallback(async () => {
    try {
      const status = await apiFetch(API_CONFIG.ENDPOINTS.ATTENDANCE_STATUS);
      setOpenSession(status.session);
      setTodayMinutes(status.today_minutes);
      setIsFaceScanned(Boolean(status.face_verified_until));
      setPresence(status.session ? (status.presence ?? status.session.state ?? null) : null);
      if (status.heartbeat_interval_seconds > 0) {
        setHeartbeatSeconds(status.heartbeat_interval_seconds);
        SecureStore.setItemAsync(HEARTBEAT_INTERVAL_KEY, String(status.heartbeat_interval_seconds)).catch(() => {});
      }
    } catch (error) {
      console.warn("Could not load attendance status", error);
    }
  }, []);

  useEffect(() => {
    if (isLoggedIn) refreshStatus();
  }, [isLoggedIn, refreshStatus]);

  // Catch up with the server when returning from the background (the background task
  // may have changed or closed the session meanwhile).
  useEffect(() => {
    if (!isLoggedIn) return;
    const sub = AppState.addEventListener("change", (next) => {
      if (next === "active") {
        refreshStatus();
        refreshLocationPermissions();
      }
    });
    return () => sub.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoggedIn, refreshStatus]);

  // ---- my beacons --------------------------------------------------------

  useEffect(() => {
    if (!isLoggedIn) return;
    let cancelled = false;
    (async () => {
      try {
        const mine: Beacon[] = await apiFetch(API_CONFIG.ENDPOINTS.MY_BEACONS);
        if (cancelled) return;
        setBeacons(mine);
        // The background task has no React state; it reads the beacons from here.
        await SecureStore.setItemAsync(BEACONS_KEY, JSON.stringify(mine), SECURE_BACKGROUND_OPTIONS);
      } catch (error) {
        console.warn("Could not load beacons", error);
        // Offline: fall back to the copy from the last login.
        try {
          const saved = await SecureStore.getItemAsync(BEACONS_KEY);
          if (saved && !cancelled) setBeacons(JSON.parse(saved));
        } catch {
          /* ignore */
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isLoggedIn]);

  // ---- BLE scanning (foreground) ----------------------------------------

  const enableScanning = useCallback(async () => {
    const granted = await requestBlePermissions();
    if (!granted) {
      setBleStatus("no-permission");
      return;
    }
    setScanRequest((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!isLoggedIn || beacons.length === 0) {
      setBleStatus("idle");
      return;
    }
    let cancelled = false;
    let stop: (() => void) | null = null;
    let flush: ReturnType<typeof setInterval> | null = null;

    (async () => {
      try {
        if (!(await hasBlePermissions())) {
          if (!cancelled) setBleStatus("no-permission");
          return;
        }
        if (!(await isBluetoothOn())) {
          if (!cancelled) setBleStatus("bluetooth-off");
          return;
        }
        if (cancelled) return;
        setBleError(null);
        stop = startBeaconScan(
          (s) => {
            sightingsRef.current.push(s);
          },
          (message) => {
            setBleError(message);
            setBleStatus("error");
          }
        );
        setBleStatus("scanning");
        // Adverts arrive several times a second; publish to React state once a second.
        flush = setInterval(() => {
          sightingsRef.current = pruneSightings(sightingsRef.current, Date.now());
          setSightings(sightingsRef.current.slice());
        }, SIGHTINGS_FLUSH_MS);
      } catch (e: any) {
        if (!cancelled) {
          setBleError(e?.message ?? "Bluetooth is unavailable");
          setBleStatus("error");
        }
      }
    })();

    return () => {
      cancelled = true;
      stop?.();
      if (flush) clearInterval(flush);
    };
  }, [isLoggedIn, beacons, scanRequest]);

  const isNearBeacon = useMemo(() => {
    const now = Date.now();
    return beacons.some((b) => beaconStatus(latestForBeacon(b, sightings, now)) === "near");
  }, [beacons, sightings]);

  /** Best RSSI per beacon over the last ~30 s, in the shape the API wants. */
  const currentReadings = useCallback((): BeaconReading[] => bestReadings(sightingsRef.current, Date.now()), []);

  // ---- location permissions & background tracking -------------------------

  const refreshLocationPermissions = useCallback(async () => {
    try {
      setLocationPermissions(await getLocationPermissionState());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (isLoggedIn) refreshLocationPermissions();
  }, [isLoggedIn, refreshLocationPermissions]);

  // Run the background task only while checked in (and allowed to).
  useEffect(() => {
    if (!isLoggedIn) return;
    if (openSession?.source === "app" && locationPermissions.background) {
      startBackgroundTracking(heartbeatSeconds).catch((e) => console.warn("Background tracking failed", e));
    } else {
      stopBackgroundTracking();
    }
  }, [isLoggedIn, openSession?.id, openSession?.source, locationPermissions.background, heartbeatSeconds]);

  // ---- check-in / check-out ---------------------------------------------

  const checkIn = useCallback(async () => {
    const coords = lastCoords.current;
    if (!coords || busy.current) return;
    busy.current = true;
    try {
      await apiFetch(API_CONFIG.ENDPOINTS.CHECK_IN, {
        json: {
          latitude: coords.latitude,
          longitude: coords.longitude,
          accuracy: coords.accuracy ?? null,
          beacons: currentReadings(),
        },
      });
      setAttendanceMessage("Checked in");
    } catch (error: any) {
      setAttendanceMessage(error.message);
      if (error.status === 403) setIsFaceScanned(false);
    } finally {
      busy.current = false;
      refreshStatus();
    }
  }, [currentReadings, refreshStatus]);

  const checkOut = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const coords = lastCoords.current;
      await apiFetch(API_CONFIG.ENDPOINTS.CHECK_OUT, {
        json: coords ? { latitude: coords.latitude, longitude: coords.longitude } : {},
      });
      setAttendanceMessage("Checked out");
    } catch (error: any) {
      setAttendanceMessage(error.message);
    } finally {
      busy.current = false;
      refreshStatus();
    }
  }, [refreshStatus]);

  // The server decides; the app only asks at the right moments.
  useEffect(() => {
    if (!isLoggedIn) return;
    if (isInsideGeofence && isFaceScanned && !openSession) checkIn();
    if (!isInsideGeofence && openSession && openSession.source === "app") checkOut();
  }, [isLoggedIn, isInsideGeofence, isFaceScanned, openSession, checkIn, checkOut]);

  // ---- foreground heartbeats --------------------------------------------

  const sessionId = openSession?.source === "app" ? openSession.id : null;

  useEffect(() => {
    if (!isLoggedIn || !sessionId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      let nextSeconds = heartbeatSecondsRef.current;
      try {
        let coords: Coords | null = lastCoords.current;
        try {
          const position = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
          coords = {
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            accuracy: position.coords.accuracy,
          };
          lastCoords.current = coords;
        } catch {
          // fall back to the last position the geofence watcher saw
        }
        if (coords && !cancelled) {
          const result = await sendHeartbeat(coords, currentReadings());
          if (cancelled) return;
          if (result.next_heartbeat_seconds > 0) nextSeconds = result.next_heartbeat_seconds;
          setPresence(result.checked_in ? result.state : null);
          if (!result.checked_in) {
            // The server closed the session (we are outside the office).
            setOpenSession(null);
            setAttendanceMessage(
              result.state === "outside"
                ? "You are outside the office area, so your work session was closed."
                : "Your work session was closed."
            );
          } else {
            setOpenSession(result.session);
          }
          refreshStatus();
        }
      } catch (error: any) {
        if (error?.status === 409) {
          // Not checked in any more (closed elsewhere).
          setOpenSession(null);
          setPresence(null);
          refreshStatus();
          return;
        }
        console.warn("Heartbeat failed", error);
      }
      if (!cancelled) timer = setTimeout(tick, nextSeconds * 1000);
    };

    timer = setTimeout(tick, heartbeatSecondsRef.current * 1000);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [isLoggedIn, sessionId, currentReadings, refreshStatus]);

  // ---- geofence callbacks & logout ----------------------------------------

  const handleEntry = useCallback((coords: Coords) => {
    lastCoords.current = coords;
    setIsInsideGeofence(true);
  }, []);

  const handleExit = useCallback((coords: Coords) => {
    lastCoords.current = coords;
    setIsInsideGeofence(false);
    setIsFaceScanned(false);
  }, []);

  const handleLogout = async () => {
    await stopBackgroundTracking();
    await clearSession();
    setIsLoggedIn(false);
    setIsFaceScanned(false);
    setIsInsideGeofence(false);
    setOpenSession(null);
    setTodayMinutes(0);
    setPresence(null);
    setBeacons([]);
    setSightings([]);
    sightingsRef.current = [];
    setTimeout(() => router.replace("/"), 0);
  };

  useEffect(() => {
    if (attendanceMessage && !["Checked in", "Checked out"].includes(attendanceMessage)) {
      Alert.alert("Attendance", attendanceMessage);
    }
  }, [attendanceMessage]);

  return (
    <AppContext.Provider
      value={{
        isInsideGeofence,
        isFaceScanned,
        setIsFaceScanned,
        handleEntry,
        handleExit,
        isCheckedIn: Boolean(openSession),
        openSession,
        todayMinutes,
        presence,
        attendanceMessage,
        refreshStatus,
        beacons,
        sightings,
        bleStatus,
        bleError,
        enableScanning,
        isNearBeacon,
        locationPermissions,
        refreshLocationPermissions,
        isLoggedIn,
        setIsLoggedIn,
        handleLogout,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};
