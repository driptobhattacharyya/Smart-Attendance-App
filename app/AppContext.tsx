import React, { createContext, useState, useEffect, ReactNode } from "react";
import * as SecureStore from "expo-secure-store";
import { useRouter } from "expo-router";

// Define types for the context
interface AppContextType {
  isInsideGeofence: boolean;
  isFaceScanned: boolean;
  isInBluetoothRange: boolean;
  setIsInBluetoothRange: React.Dispatch<React.SetStateAction<boolean>>;
  setIsFaceScanned: React.Dispatch<React.SetStateAction<boolean>>;
  entryTimes: string[];
  exitTimes: string[];
  setEntryTimes: React.Dispatch<React.SetStateAction<string[]>>;
  setExitTimes: React.Dispatch<React.SetStateAction<string[]>>;
  handleEntry: () => void;
  handleExit: () => void;
  accumulatedTime: number;
  setAccumulatedTime: React.Dispatch<React.SetStateAction<number>>;
  isLoggedIn: boolean;
  setIsLoggedIn: React.Dispatch<React.SetStateAction<boolean>>;
  handleLogout: () => Promise<void>;
}

// Create the context with a default value
export const AppContext = createContext<AppContextType | undefined>(undefined);

// Define the props for the AppProvider component
interface AppProviderProps {
  children: ReactNode;
}

// The AppProvider component
export const AppProvider: React.FC<AppProviderProps> = ({ children }) => {
  const router = useRouter();
  const [isInsideGeofence, setIsInsideGeofence] = useState<boolean>(false);
  const [isFaceScanned, setIsFaceScanned] = useState(false);
  const [entryTimes, setEntryTimes] = useState<string[]>([]);
  const [exitTimes, setExitTimes] = useState<string[]>([]);
  const [accumulatedTime, setAccumulatedTime] = useState(0);
  const [isInBluetoothRange, setIsInBluetoothRange] = useState(false);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  // Function to handle geofence entry
  const handleEntry = () => {
    if (!isInsideGeofence) {
      setIsInsideGeofence(true);
      setEntryTimes((prev) => [...prev, new Date().toISOString()]);
    }
  };

  // Function to handle geofence exit
  const handleExit = () => {
    console.log("Handle Exit triggered 1");
    console.log("Handle Exit triggered 2");
    setIsInsideGeofence(false);
    setExitTimes((prev) => [...prev, new Date().toISOString()]);
    setIsFaceScanned(false);
    console.log("is Inside Geofence:", isInsideGeofence);
  };

  // Function to handle logout
  const handleLogout = async () => {
    try {
      // First clear the secure store
      await SecureStore.deleteItemAsync("employee_id");
      
      // Then reset all state
      setIsLoggedIn(false);
      setIsFaceScanned(false);
      setIsInBluetoothRange(false);
      setIsInsideGeofence(false);
      setEntryTimes([]);
      setExitTimes([]);
      setAccumulatedTime(0);

      // Use setTimeout to ensure state updates have propagated
      setTimeout(() => {
        router.replace("/");
      }, 0);
    } catch (error) {
      console.error("Error during logout:", error);
      throw error;
    }
  };

  useEffect(() => {
    // Debug logs to ensure the state updates correctly
    console.log("Geofence Status:", isInsideGeofence ? "Inside" : "Outside");
    console.log("Entry Times:", entryTimes);
    console.log("Exit Times:", exitTimes);
  }, [isInsideGeofence, entryTimes, exitTimes]);

  return (
    <AppContext.Provider
      value={{
        isInsideGeofence,
        isFaceScanned,
        setIsFaceScanned,
        entryTimes,
        exitTimes,
        setEntryTimes,
        setExitTimes,
        handleEntry,
        handleExit,
        accumulatedTime,
        setAccumulatedTime,
        isInBluetoothRange,
        setIsInBluetoothRange,
        isLoggedIn,
        setIsLoggedIn,
        handleLogout,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};
