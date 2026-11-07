import React, { createContext, useCallback, useEffect, useRef, useState, ReactNode } from "react";
import { Alert } from "react-native";
import { useRouter } from "expo-router";
import * as SecureStore from "expo-secure-store";
import { apiFetch, clearSession } from "@/lib/api";
import { API_CONFIG } from "@/lib/config";

export type Coords = { latitude: number; longitude: number; accuracy?: number | null };

export type Session = {
  id: string;
  date: string;
  started_at: string;
  ended_at: string | null;
  source: "app" | "request";
  minutes: number;
};

interface AppContextType {
  isInsideGeofence: boolean;
  isFaceScanned: boolean;
  setIsFaceScanned: React.Dispatch<React.SetStateAction<boolean>>;
  isInBluetoothRange: boolean;
  setIsInBluetoothRange: React.Dispatch<React.SetStateAction<boolean>>;
  setBluetoothRssi: React.Dispatch<React.SetStateAction<number | null>>;
  /** Called by the location watcher with each new position. */
  handleEntry: (coords: Coords) => void;
  handleExit: (coords: Coords) => void;
  /** Server-side attendance state */
  isCheckedIn: boolean;
  openSession: Session | null;
  todayMinutes: number;
  attendanceMessage: string | null;
  refreshStatus: () => Promise<void>;
  isLoggedIn: boolean;
  setIsLoggedIn: React.Dispatch<React.SetStateAction<boolean>>;
  handleLogout: () => Promise<void>;
}

export const AppContext = createContext<AppContextType>(undefined as unknown as AppContextType);

export const AppProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const router = useRouter();
  const [isInsideGeofence, setIsInsideGeofence] = useState(false);
  const [isFaceScanned, setIsFaceScanned] = useState(false);
  const [isInBluetoothRange, setIsInBluetoothRange] = useState(false);
  const [bluetoothRssi, setBluetoothRssi] = useState<number | null>(null);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  const [openSession, setOpenSession] = useState<Session | null>(null);
  const [todayMinutes, setTodayMinutes] = useState(0);
  const [attendanceMessage, setAttendanceMessage] = useState<string | null>(null);
  const lastCoords = useRef<Coords | null>(null);
  const busy = useRef(false);

  const refreshStatus = useCallback(async () => {
    try {
      const status = await apiFetch(API_CONFIG.ENDPOINTS.ATTENDANCE_STATUS);
      setOpenSession(status.session);
      setTodayMinutes(status.today_minutes);
      setIsFaceScanned(Boolean(status.face_verified_until));
    } catch (error) {
      console.warn("Could not load attendance status", error);
    }
  }, []);

  useEffect(() => {
    if (isLoggedIn) refreshStatus();
  }, [isLoggedIn, refreshStatus]);

  const checkIn = useCallback(async () => {
    const coords = lastCoords.current;
    if (!coords || busy.current) return;
    busy.current = true;
    try {
      const deviceId = isInBluetoothRange
        ? await SecureStore.getItemAsync("bluetooth_device_id")
        : null;
      await apiFetch(API_CONFIG.ENDPOINTS.CHECK_IN, {
        json: {
          latitude: coords.latitude,
          longitude: coords.longitude,
          accuracy: coords.accuracy ?? null,
          bluetooth_device_id: deviceId || null,
          bluetooth_rssi: isInBluetoothRange ? bluetoothRssi : null,
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
  }, [isInBluetoothRange, bluetoothRssi, refreshStatus]);

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
    await clearSession();
    setIsLoggedIn(false);
    setIsFaceScanned(false);
    setIsInBluetoothRange(false);
    setIsInsideGeofence(false);
    setOpenSession(null);
    setTodayMinutes(0);
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
        isInBluetoothRange,
        setIsInBluetoothRange,
        setBluetoothRssi,
        handleEntry,
        handleExit,
        isCheckedIn: Boolean(openSession),
        openSession,
        todayMinutes,
        attendanceMessage,
        refreshStatus,
        isLoggedIn,
        setIsLoggedIn,
        handleLogout,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};
