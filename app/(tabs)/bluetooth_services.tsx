import React, { useState, useEffect, useContext } from "react";
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  TouchableOpacity,
  Platform,
  StatusBar,
  PermissionsAndroid,
  Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { BleManager, Device } from "react-native-ble-plx";
import { AppContext } from "../AppContext";
import * as SecureStore from "expo-secure-store";

const BluetoothScanner = () => {
  const [bleManager] = useState(new BleManager());
  const [devices, setDevices] = useState([]);
  const [showInfo, setShowInfo] = useState(false);
  const {isInBluetoothRange, setIsInBluetoothRange} = useContext(AppContext);
  const [lastMessage, setLastMessage] = useState("");
  const [targetDeviceID, setTargetDeviceID] = useState<string | null>(null);

  // Load target device ID from SecureStore
  useEffect(() => {
    const loadTargetDevice = async () => {
      try {
        const savedDeviceId = await SecureStore.getItemAsync('bluetooth_device_id');
        console.log("Saved device ID:", savedDeviceId);
        if (savedDeviceId) {
          setTargetDeviceID(savedDeviceId);
        } else {
          console.log('No saved Bluetooth device ID found');
        }
      } catch (error) {
        console.error('Error loading Bluetooth device ID:', error);
      }
      console.log("Target device ID:", targetDeviceID);
    };

    loadTargetDevice();
  }, []);

  useEffect(() => {
    const startScan = async () => {
      // Request permissions on Android
      if (Platform.OS === "android") {
        const granted = await PermissionsAndroid.requestMultiple([
          PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION,
          PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN,
          PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT,
        ]);

        if (
          granted["android.permission.ACCESS_FINE_LOCATION"] !== "granted" ||
          granted["android.permission.BLUETOOTH_SCAN"] !== "granted"
        ) {
          Alert.alert("Permissions not granted!");
          return;
        }
      }

      // Start scanning for devices
      bleManager.startDeviceScan(null, null, (error, scannedDevice) => {
        if (error) {
          console.log("Error during scan:", error);
          return;
        }

        if (scannedDevice && scannedDevice.rssi) {
          setDevices((prevDevices) => {
            const currentTime = Date.now();

            // Check if the device already exists in the list
            const existingDevice = prevDevices.find((d) => d.id === scannedDevice.id);

            if (existingDevice) {
              // Update RSSI and lastSeen timestamp for existing device
              return prevDevices.map((d) =>
                d.id === scannedDevice.id
                  ? { ...d, rssi: scannedDevice.rssi, lastSeen: currentTime }
                  : d
              );
            } else {
              // Add new device to the list with lastSeen timestamp
              return [
                ...prevDevices,
                { ...scannedDevice, lastSeen: currentTime },
              ];
            }
          });
        }
      });
    };

    // Start the scan and repeat every few seconds
    const intervalId = setInterval(() => {
      startScan();
    }, 15000);
    
    // Cleanup on component unmount
    return () => {
      bleManager.stopDeviceScan();
      bleManager.destroy();
      clearInterval(intervalId);
    };
  }, [bleManager]);

  // Remove stale devices
  useEffect(() => {
    const cleanupStaleDevices = () => {
      const currentTime = Date.now();
      const staleThreshold = 10000; // 10 seconds

      setDevices((prevDevices) =>
        prevDevices.filter((d) => currentTime - d.lastSeen <= staleThreshold)
      );
    };

    const cleanupInterval = setInterval(cleanupStaleDevices, 5000); // Check every 5 seconds

    return () => clearInterval(cleanupInterval);
  }, []);

  // Monitor target device proximity
  useEffect(() => {
    if (!targetDeviceID) return; // Don't check if no target device is set

    const targetDevice = devices.find((d) => d.id === targetDeviceID);
    
    if (targetDevice && targetDevice.rssi > -70) {
      // Device detected nearby
      if (!isInBluetoothRange) {
        setIsInBluetoothRange(true); // Update state to detected
        setLastMessage("Target device detected nearby."); // Update the message
        console.log("Target device detected nearby."); // For debugging
      }
    } else {
      // Device not detected or far away
      if (isInBluetoothRange) {
        setIsInBluetoothRange(false); // Update state to not detected
        setLastMessage("Target device not detected."); // Update the message
        console.log("Target device not detected."); // For debugging
      }
    }
  }, [devices, isInBluetoothRange, targetDeviceID]);
  
  return (
    <View style={styles.container}>
      {/* Title with Info Icon */}
      <View style={styles.titleContainer}>
        <Text style={styles.title}>Nearby Bluetooth Devices</Text>
        <TouchableOpacity onPress={() => setShowInfo(!showInfo)}>
          <Ionicons name="information-circle" size={24} color="#6A5ACD" />
        </TouchableOpacity>
      </View>

      {/* Info Explanation */}
      {showInfo && (
        <View style={styles.infoBox}>
          <Text style={styles.infoText}>
            RSSI indicates the signal strength from the device. Closer to 0 means the device is
            very close. An RSSI of -70 or better is required for attendance.
          </Text>
        </View>
      )}

      {/* Device List */}
      <FlatList
        data={devices}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <View
            style={[
              styles.deviceContainer,
              item.id === targetDeviceID && styles.targetDeviceContainer,
            ]}
          >
            <Text
              style={[
                styles.deviceName,
                item.id === targetDeviceID && styles.targetDeviceName,
              ]}
            >
              {item.name || "Unnamed Device"}
            </Text>
            <Text
              style={[
                styles.deviceRSSI,
                item.id === targetDeviceID && styles.targetDeviceRSSI,
              ]}
            >
              RSSI: {item.rssi}
            </Text>
          </View>
        )}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#F8F4FF",
    padding: 16,
    paddingTop: Platform.OS === "android" ? StatusBar.currentHeight || 24 : 24,
  },
  titleContainer: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 16,
  },
  title: {
    fontSize: 20,
    fontWeight: "600",
    color: "#4A3B94",
  },
  infoBox: {
    backgroundColor: "#E6E0FF",
    borderRadius: 8,
    padding: 10,
    marginBottom: 16,
  },
  infoText: {
    color: "#6A5ACD",
    fontSize: 14,
    lineHeight: 20,
  },
  deviceContainer: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "#EDEAFF",
    padding: 12,
    borderRadius: 8,
    marginBottom: 10,
  },
  targetDeviceContainer: {
    backgroundColor: "#D1CFFF",
    borderWidth: 1,
    borderColor: "#6A5ACD",
  },
  deviceName: {
    fontSize: 16,
    color: "#4A3B94",
  },
  targetDeviceName: {
    fontWeight: "bold",
    color: "#6A5ACD",
  },
  deviceRSSI: {
    fontSize: 14,
    color: "#4A3B94",
  },
  targetDeviceRSSI: {
    fontWeight: "bold",
    color: "#6A5ACD",
  },
});

export default BluetoothScanner;