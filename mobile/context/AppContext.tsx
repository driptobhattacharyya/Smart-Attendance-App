import React, { createContext, useCallback, useEffect, useMemo, useRef, useState, ReactNode } from "react";
import { Alert, AppState } from "react-native";
import { useRouter } from "expo-router";
import * as Location from "expo-location";
import * as SecureStore from "expo-secure-store";
import {
  clearSession,
  DESK_DEVICES_KEY,
  getRefreshToken,
  HEARTBEAT_INTERVAL_KEY,
  isConsentRequiredError,
  isNetworkError,
  loadJson,
  newIdempotencyKey,
  PROFILE_CACHE_KEY,
  revokeSession,
  setAuthLostListener,
  setConsentRequiredListener,
  storeJson,
  apiFetch,
} from "@/lib/api";
import { API_CONFIG } from "@/lib/config";
import {
  buildCheckInBody,
  buildCheckOutBody,
  checkIn as postCheckIn,
  checkOut as postCheckOut,
  fetchStatus,
  PresenceState,
  SessionOut,
} from "@/lib/attendanceApi";
import { collectTrust } from "@/lib/collectTrust";
import { hasBlePermissions, isBluetoothOn, requestBlePermissions, startBeaconScan } from "@/lib/ble";
import {
  Beacon,
  beaconStatus,
  BeaconReading,
  DeskDeviceDto,
  latestForBeacon,
  normalizeDeskDevices,
  pruneSightings,
  readingsForDevices,
  Sighting,
} from "@/lib/beacons";
import {
  DEFAULT_HEARTBEAT_SECONDS,
  getLocationPermissionState,
  LocationPermissionState,
  sendHeartbeat,
  startBackgroundTracking,
  stopBackgroundTracking,
} from "@/lib/presence";
import { hasPermission, isModuleOn, loadProfile, Modules, Office, Profile } from "@/lib/profile";

export type Session = SessionOut;

export type BleStatus = "idle" | "scanning" | "no-permission" | "bluetooth-off" | "error";

interface AppContextType {
  isInsideGeofence: boolean;
  /** True once the face check passed (or when the organisation has switched the face module off). */
  isFaceScanned: boolean;
  setIsFaceScanned: React.Dispatch<React.SetStateAction<boolean>>;
  /** Called by the location watcher with each new position. */
  handleEntry: (position: Location.LocationObject) => void;
  handleExit: (position: Location.LocationObject) => void;
  /** Server-side attendance state */
  isCheckedIn: boolean;
  openSession: Session | null;
  todayMinutes: number;
  /** present / away / outside as last decided by the server (null when not checked in). */
  presence: PresenceState | null;
  attendanceMessage: string | null;
  refreshStatus: () => Promise<void>;
  /** Desk devices the phone scans for (GET /places/desk-devices/mine); empty when proximity is off. */
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
  /** Load the profile after tokens were saved (login / invite) and enter the app. */
  startSession: () => Promise<void>;
  /** Try to resume a saved session at app start. Resolves when the app can show a screen. */
  restoreSession: () => Promise<void>;
  /** Set when the session ended on its own (refresh failed); shown on the login screen. */
  authNotice: string | null;
  clearAuthNotice: () => void;
  /**
   * True while the logged-in user has not accepted the current privacy notice. Nothing
   * that uses location, Bluetooth or the camera runs while it is true.
   */
  consentRequired: boolean;
  setConsentRequired: (required: boolean) => void;
  /** False until the user has enrolled a face; the app shows the enrolment screen until then. */
  faceEnrolled: boolean;
  setFaceEnrolled: (enrolled: boolean) => void;
  profile: Profile | null;
  modules: Modules;
  office: Office | null;
  can: (permission: string) => boolean;
  /** Module switches (organisation settings). */
  faceRequired: boolean;
  proximityEnabled: boolean;
  wfhEnabled: boolean;
  geofenceEnabled: boolean;
  /** The user asked to work remotely today (module wfh): face check, then a remote check-in. */
  remoteRequested: boolean;
  setRemoteRequested: (requested: boolean) => void;
  handleLogout: () => Promise<void>;
}

export const AppContext = createContext<AppContextType>(undefined as unknown as AppContextType);

const SIGHTINGS_FLUSH_MS = 1000;

/** Server codes meaning "do the face check again". */
const FACE_AGAIN_CODES = ["face_required", "face_verification_required", "face_expired"];

export const AppProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const router = useRouter();
  const [isInsideGeofence, setIsInsideGeofence] = useState(false);
  const [faceScanned, setIsFaceScanned] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [consentRequired, setConsentRequiredState] = useState(false);
  const [faceEnrolled, setFaceEnrolledState] = useState(false);
  const [remoteRequested, setRemoteRequested] = useState(false);

  const modules: Modules = profile?.modules ?? {};
  const faceRequired = isModuleOn(modules, "face");
  const proximityEnabled = isModuleOn(modules, "proximity");
  const wfhEnabled = isModuleOn(modules, "wfh");
  const geofenceEnabled = isModuleOn(modules, "geofence");
  const office = profile?.office ?? null;
  const isFaceScanned = faceScanned || !faceRequired;

  const updateConsent = useCallback((required: boolean) => setConsentRequiredState(required), []);
  const updateFaceEnrolled = useCallback((enrolled: boolean) => setFaceEnrolledState(enrolled), []);

  // Keep a copy of the profile for starting up offline.
  useEffect(() => {
    if (isLoggedIn && profile) storeJson(PROFILE_CACHE_KEY, { ...profile, consentRequired, faceEnrolled });
  }, [isLoggedIn, profile, consentRequired, faceEnrolled]);

  // Any 403 consent_required from the API reopens the consent screen.
  useEffect(() => {
    setConsentRequiredListener(() => updateConsent(true));
    return () => setConsentRequiredListener(null);
  }, [updateConsent]);

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

  const lastPosition = useRef<Location.LocationObject | null>(null);
  const busy = useRef(false);
  const sightingsRef = useRef<Sighting[]>([]);
  const beaconsRef = useRef<Beacon[]>([]);
  beaconsRef.current = beacons;
  const heartbeatSecondsRef = useRef(heartbeatSeconds);
  heartbeatSecondsRef.current = heartbeatSeconds;

  // ---- session start / restore / end -------------------------------------

  const applyProfile = useCallback((p: Profile) => {
    setProfile(p);
    setConsentRequiredState(p.consentRequired);
    setFaceEnrolledState(p.faceEnrolled);
  }, []);

  const startSession = useCallback(async () => {
    try {
      applyProfile(await loadProfile(false));
      setAuthNotice(null);
      setIsLoggedIn(true);
    } catch (error) {
      await clearSession();
      throw error;
    }
  }, [applyProfile]);

  const restoreSession = useCallback(async () => {
    if (!(await getRefreshToken())) {
      setIsLoggedIn(false);
      return;
    }
    const cached = await loadJson<Profile>(PROFILE_CACHE_KEY);
    try {
      applyProfile(await loadProfile(Boolean(cached?.faceEnrolled)));
      setIsLoggedIn(true);
    } catch (error) {
      // Offline (or the server is down): keep the user signed in with the last known profile.
      // If the session itself ended, the auth-lost listener has already shown the login screen.
      const offline = isNetworkError(error) || (error as any)?.status >= 500;
      if (offline && cached?.me) {
        applyProfile(cached);
        setIsLoggedIn(true);
      } else {
        setIsLoggedIn(false);
      }
    }
  }, [applyProfile]);

  const resetState = useCallback(async () => {
    await stopBackgroundTracking();
    setIsLoggedIn(false);
    setProfile(null);
    setConsentRequiredState(false);
    setFaceEnrolledState(false);
    setIsFaceScanned(false);
    setRemoteRequested(false);
    setIsInsideGeofence(false);
    setOpenSession(null);
    setTodayMinutes(0);
    setPresence(null);
    setBeacons([]);
    setSightings([]);
    sightingsRef.current = [];
    lastPosition.current = null;
  }, []);

  // The refresh token no longer works: back to the login screen with a message.
  useEffect(() => {
    setAuthLostListener((message) => {
      setAuthNotice(message);
      clearSession().catch(() => {});
      resetState().then(() => setTimeout(() => router.replace("/"), 0));
    });
    return () => setAuthLostListener(null);
  }, [resetState, router]);

  const handleLogout = async () => {
    await stopBackgroundTracking();
    await revokeSession(); // POST /auth/logout, then wipe the local tokens and caches
    await resetState();
    setTimeout(() => router.replace("/"), 0);
  };

  // ---- attendance status -------------------------------------------------

  const refreshStatus = useCallback(async () => {
    try {
      const status = await fetchStatus();
      setOpenSession(status.session);
      setTodayMinutes(status.today_minutes);
      setIsFaceScanned(Boolean(status.face_verified_until));
      setPresence(status.session ? (status.presence ?? status.session.state ?? null) : null);
      if (status.heartbeat_interval_seconds && status.heartbeat_interval_seconds > 0) {
        setHeartbeatSeconds(status.heartbeat_interval_seconds);
        SecureStore.setItemAsync(HEARTBEAT_INTERVAL_KEY, String(status.heartbeat_interval_seconds)).catch(() => {});
      }
    } catch (error) {
      console.warn("Could not load attendance status", error);
    }
  }, []);

  useEffect(() => {
    if (isLoggedIn && !consentRequired) refreshStatus();
  }, [isLoggedIn, consentRequired, refreshStatus]);

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

  // ---- desk devices to scan for -------------------------------------------

  useEffect(() => {
    if (!isLoggedIn || !profile) return;
    let cancelled = false;
    (async () => {
      if (!proximityEnabled) {
        // Module off: nothing is scanned or sent, and the background task sees an empty list.
        setBeacons([]);
        await storeJson(DESK_DEVICES_KEY, []);
        return;
      }
      try {
        const mine = normalizeDeskDevices(await apiFetch<DeskDeviceDto[]>(API_CONFIG.ENDPOINTS.DESK_DEVICES_MINE));
        if (cancelled) return;
        setBeacons(mine);
        // The background task has no React state; it reads the devices from here.
        await storeJson(DESK_DEVICES_KEY, mine);
      } catch (error) {
        if (isConsentRequiredError(error)) return;
        console.warn("Could not load desk devices", error);
        // Offline: fall back to the copy from the last time.
        const saved = await loadJson<Beacon[]>(DESK_DEVICES_KEY);
        if (saved && !cancelled) setBeacons(saved);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isLoggedIn, profile, proximityEnabled]);

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
    if (!isLoggedIn || consentRequired || beacons.length === 0) {
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
  }, [isLoggedIn, consentRequired, beacons, scanRequest]);

  const isNearBeacon = useMemo(() => {
    const now = Date.now();
    return beacons.some((b) => beaconStatus(latestForBeacon(b, sightings, now)) === "near");
  }, [beacons, sightings]);

  /**
   * Best RSSI per beacon identity over the last ~30 s, only for identities that match one of
   * my desk devices (null major/minor = wildcard, for rotating desk apps).
   */
  const currentReadings = useCallback(
    (): BeaconReading[] => readingsForDevices(sightingsRef.current, beaconsRef.current, Date.now()),
    []
  );

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
    if (!consentRequired && openSession?.source === "app" && locationPermissions.background) {
      startBackgroundTracking(heartbeatSeconds).catch((e) => console.warn("Background tracking failed", e));
    } else {
      stopBackgroundTracking();
    }
  }, [
    isLoggedIn,
    consentRequired,
    openSession?.id,
    openSession?.source,
    locationPermissions.background,
    heartbeatSeconds,
  ]);

  // ---- check-in / check-out ---------------------------------------------

  /** A fresh fix if the GPS answers, else the last one the geofence watcher saw. */
  const currentPosition = useCallback(async (): Promise<Location.LocationObject | null> => {
    try {
      const position = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      lastPosition.current = position;
      return position;
    } catch {
      return lastPosition.current;
    }
  }, []);

  const checkIn = useCallback(
    async (mode: "onsite" | "remote") => {
      if (busy.current) return;
      busy.current = true;
      try {
        const position = await currentPosition();
        if (!position) {
          setAttendanceMessage("Could not get your location. Turn on location services and try again.");
          return;
        }
        const trust = await collectTrust(position);
        // One key per attempt: the HTTP client reuses it if it has to retry.
        await postCheckIn(buildCheckInBody({ trust, beacons: currentReadings(), mode }), newIdempotencyKey());
        setAttendanceMessage("Checked in");
      } catch (error: any) {
        // A consent error reopens the consent screen (see the listener above); no alert needed.
        if (!isConsentRequiredError(error)) setAttendanceMessage(error.message);
        if (FACE_AGAIN_CODES.includes(error?.code)) setIsFaceScanned(false);
      } finally {
        busy.current = false;
        refreshStatus();
      }
    },
    [currentPosition, currentReadings, refreshStatus]
  );

  const checkOut = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const position = lastPosition.current;
      const trust = position ? await collectTrust(position) : null;
      await postCheckOut(buildCheckOutBody(trust), newIdempotencyKey());
      setAttendanceMessage("Checked out");
    } catch (error: any) {
      setAttendanceMessage(error.message);
    } finally {
      busy.current = false;
      refreshStatus();
    }
  }, [refreshStatus]);

  // The server decides; the app only asks at the right moments.
  const isRemoteSession = openSession?.mode === "remote";
  const effectivelyInside = !geofenceEnabled || isInsideGeofence;
  useEffect(() => {
    if (!isLoggedIn || consentRequired || !profile) return;
    if (!openSession && isFaceScanned) {
      if (remoteRequested) checkIn("remote");
      else if (effectivelyInside) checkIn("onsite");
    }
    if (geofenceEnabled && !isInsideGeofence && openSession && openSession.source === "app" && !isRemoteSession) {
      checkOut();
    }
  }, [
    isLoggedIn,
    consentRequired,
    profile,
    geofenceEnabled,
    isInsideGeofence,
    effectivelyInside,
    isFaceScanned,
    remoteRequested,
    openSession,
    isRemoteSession,
    checkIn,
    checkOut,
  ]);

  // A remote day is over once the session is closed.
  useEffect(() => {
    if (remoteRequested && !openSession && attendanceMessage === "Checked out") setRemoteRequested(false);
  }, [remoteRequested, openSession, attendanceMessage]);

  // ---- foreground heartbeats --------------------------------------------

  const sessionId = openSession?.source === "app" ? openSession.id : null;

  useEffect(() => {
    if (!isLoggedIn || consentRequired || !sessionId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      let nextSeconds = heartbeatSecondsRef.current;
      try {
        const position = await currentPosition();
        if (position && !cancelled) {
          const result = await sendHeartbeat(position, currentReadings());
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
        // Consent was withdrawn or never given: the listener shows the consent screen and this loop stops.
        if (isConsentRequiredError(error)) return;
        if (error?.status === 409) {
          // Not checked in any more (closed elsewhere).
          setOpenSession(null);
          setPresence(null);
          refreshStatus();
          return;
        }
        if (error?.code === "session_expired") return; // the auth-lost listener takes over
        console.warn("Heartbeat failed", error);
      }
      if (!cancelled) timer = setTimeout(tick, nextSeconds * 1000);
    };

    timer = setTimeout(tick, heartbeatSecondsRef.current * 1000);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [isLoggedIn, consentRequired, sessionId, currentPosition, currentReadings, refreshStatus]);

  // ---- geofence callbacks ---------------------------------------------------

  const handleEntry = useCallback((position: Location.LocationObject) => {
    lastPosition.current = position;
    setIsInsideGeofence(true);
  }, []);

  const handleExit = useCallback((position: Location.LocationObject) => {
    lastPosition.current = position;
    setIsInsideGeofence(false);
    setIsFaceScanned(false);
  }, []);

  useEffect(() => {
    if (attendanceMessage && !["Checked in", "Checked out"].includes(attendanceMessage)) {
      Alert.alert("Attendance", attendanceMessage);
    }
  }, [attendanceMessage]);

  const can = useCallback((permission: string) => hasPermission(profile?.me, permission), [profile]);

  return (
    <AppContext.Provider
      value={{
        isInsideGeofence: effectivelyInside,
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
        startSession,
        restoreSession,
        authNotice,
        clearAuthNotice: () => setAuthNotice(null),
        consentRequired,
        setConsentRequired: updateConsent,
        faceEnrolled,
        setFaceEnrolled: updateFaceEnrolled,
        profile,
        modules,
        office,
        can,
        faceRequired,
        proximityEnabled,
        wfhEnabled,
        geofenceEnabled,
        remoteRequested,
        setRemoteRequested,
        handleLogout,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};
