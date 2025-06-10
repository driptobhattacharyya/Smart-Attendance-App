import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  Alert,
} from 'react-native';
import { BleManager } from 'react-native-ble-plx';
import * as SecureStore from 'expo-secure-store';

interface BluetoothDeviceSelectionProps {
  formData: any;
  setFormData: (data: any) => void;
  onNext: () => void;
}

const BluetoothDeviceSelection = ({ formData, setFormData, onNext }: BluetoothDeviceSelectionProps) => {
  const [devices, setDevices] = useState<any[]>([]);
  const [scanning, setScanning] = useState(false);
  const [selectedDevice, setSelectedDevice] = useState<any>(null);
  const bleManager = new BleManager();

  useEffect(() => {
    // Load saved device ID on mount
    const loadSavedDevice = async () => {
      try {
        const savedDeviceId = await SecureStore.getItemAsync('bluetooth_device_id');
        if (savedDeviceId) {
          // Find the device in the scanned devices
          const device = devices.find(d => d.id === savedDeviceId);
          if (device) {
            setSelectedDevice(device);
            setFormData({
              ...formData,
              device_id: device.id,
              device_name: device.name || 'Unknown Device'
            });
          }
        }
      } catch (error) {
        console.error('Error loading saved device:', error);
      }
    };

    loadSavedDevice();
    return () => {
      bleManager.destroy();
    };
  }, [devices]);

  const startScan = async () => {
    try {
      setScanning(true);
      setDevices([]);

      bleManager.startDeviceScan(null, null, (error, device) => {
        if (error) {
          console.error(error);
          return;
        }

        if (device) {
          setDevices(prevDevices => {
            const existingDevice = prevDevices.find(d => d.id === device.id);
            if (!existingDevice) {
              return [...prevDevices, device];
            }
            return prevDevices;
          });
        }
      });

      // Stop scanning after 10 seconds
      setTimeout(() => {
        bleManager.stopDeviceScan();
        setScanning(false);
      }, 10000);
    } catch (error) {
      console.error('Error scanning for devices:', error);
      Alert.alert('Error', 'Failed to scan for Bluetooth devices');
      setScanning(false);
    }
  };

  const selectDevice = (device: any) => {
    setSelectedDevice(device);
    setFormData({
      ...formData,
      device_id: device.id,
      device_name: device.name || 'Unknown Device'
    });
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Select Bluetooth Device</Text>
      <Text style={styles.subtitle}>
        Choose the Bluetooth device you want to connect to
      </Text>

      <TouchableOpacity
        style={[styles.scanButton, scanning && styles.scanningButton]}
        onPress={startScan}
        disabled={scanning}
      >
        <Text style={styles.scanButtonText}>
          {scanning ? 'Scanning...' : 'Scan for Devices'}
        </Text>
      </TouchableOpacity>

      <FlatList
        data={devices}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <TouchableOpacity
            style={[
              styles.deviceItem,
              selectedDevice?.id === item.id && styles.selectedDevice
            ]}
            onPress={() => selectDevice(item)}
          >
            <Text style={styles.deviceName}>
              {item.name || 'Unknown Device'}
            </Text>
            <Text style={styles.deviceId}>{item.id}</Text>
          </TouchableOpacity>
        )}
        style={styles.deviceList}
      />

      {selectedDevice && (
        <TouchableOpacity
          style={styles.nextButton}
          onPress={onNext}
        >
          <Text style={styles.nextButtonText}>Next</Text>
        </TouchableOpacity>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 20,
    backgroundColor: '#f4eefe',
  },
  title: {
    fontSize: 24,
    fontWeight: 'bold',
    color: '#442c91',
    marginBottom: 10,
  },
  subtitle: {
    fontSize: 16,
    color: '#666',
    marginBottom: 20,
  },
  scanButton: {
    backgroundColor: '#442c91',
    padding: 15,
    borderRadius: 8,
    alignItems: 'center',
    marginBottom: 20,
  },
  scanningButton: {
    backgroundColor: '#666',
  },
  scanButtonText: {
    color: 'white',
    fontSize: 16,
    fontWeight: 'bold',
  },
  deviceList: {
    flex: 1,
  },
  deviceItem: {
    backgroundColor: 'white',
    padding: 15,
    borderRadius: 8,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: '#ddd',
  },
  selectedDevice: {
    borderColor: '#442c91',
    borderWidth: 2,
  },
  deviceName: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#333',
  },
  deviceId: {
    fontSize: 12,
    color: '#666',
    marginTop: 5,
  },
  nextButton: {
    backgroundColor: '#442c91',
    padding: 15,
    borderRadius: 8,
    alignItems: 'center',
    marginTop: 20,
  },
  nextButtonText: {
    color: 'white',
    fontSize: 16,
    fontWeight: 'bold',
  },
});

export default BluetoothDeviceSelection; 