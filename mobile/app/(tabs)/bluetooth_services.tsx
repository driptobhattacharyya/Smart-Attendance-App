import React, { useContext, useState } from "react";
import { View, Text, FlatList, StyleSheet, TouchableOpacity, Platform, StatusBar } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { AppContext, BleStatus } from "@/context/AppContext";
import { Beacon, beaconStatus, BeaconStatus, DEFAULT_MIN_RSSI, latestForBeacon } from "@/lib/beacons";

const STATUS_STYLE: Record<BeaconStatus, { label: string; color: string; background: string }> = {
  near: { label: "Near", color: "#1B7F3B", background: "#DDF5E4" },
  far: { label: "Far", color: "#9A6200", background: "#FFF0CF" },
  "not seen": { label: "Not seen", color: "#6B6B7B", background: "#ECECF1" },
};

const BLE_MESSAGE: Partial<Record<BleStatus, string>> = {
  "no-permission": "Bluetooth permission is needed to look for your beacon.",
  "bluetooth-off": "Bluetooth is turned off. Turn it on, then tap Retry.",
  error: "Scanning failed.",
  idle: "Scanning has not started.",
};

const describeBeacon = (b: Beacon) =>
  `${b.uuid}${b.major !== null ? `  major ${b.major}` : "  any major"}${b.minor !== null ? `  minor ${b.minor}` : "  any minor"}`;

const BluetoothScanner = () => {
  const { beacons, sightings, bleStatus, bleError, enableScanning } = useContext(AppContext);
  const [showInfo, setShowInfo] = useState(false);
  const now = Date.now();

  const rows = beacons.map((beacon) => {
    const sighting = latestForBeacon(beacon, sightings, now);
    return { beacon, sighting, status: beaconStatus(sighting) };
  });

  const problem = bleStatus !== "scanning" && beacons.length > 0 ? BLE_MESSAGE[bleStatus] : null;

  return (
    <View style={styles.container}>
      <View style={styles.titleContainer}>
        <Text style={styles.title}>Your beacons</Text>
        <TouchableOpacity onPress={() => setShowInfo(!showInfo)} accessibilityLabel="About beacons">
          <Ionicons name="information-circle" size={24} color="#6A5ACD" />
        </TouchableOpacity>
      </View>

      {showInfo && (
        <View style={styles.infoBox}>
          <Text style={styles.infoText}>
            Your admin placed a Bluetooth beacon at your desk. Your time only counts as "at your desk" while the
            phone hears it with a signal of {DEFAULT_MIN_RSSI} dBm or better (closer to 0 is stronger).
            {Platform.OS === "ios"
              ? "\n\nOn iPhone, background Bluetooth scanning is limited by iOS, so beacons may only be detected while the app is open."
              : ""}
          </Text>
        </View>
      )}

      {problem && (
        <View style={styles.warningBox}>
          <Text style={styles.warningText}>
            {problem}
            {bleStatus === "error" && bleError ? ` ${bleError}` : ""}
          </Text>
          <TouchableOpacity style={styles.retryButton} onPress={enableScanning}>
            <Text style={styles.retryText}>{bleStatus === "no-permission" ? "Allow Bluetooth" : "Retry"}</Text>
          </TouchableOpacity>
        </View>
      )}

      <FlatList
        data={rows}
        keyExtractor={(item) => item.beacon.id}
        ListEmptyComponent={
          <Text style={styles.emptyText}>
            No beacon is assigned to you yet. Ask your admin to assign one to you or your office.
          </Text>
        }
        renderItem={({ item }) => {
          const style = STATUS_STYLE[item.status];
          return (
            <View style={styles.beaconContainer}>
              <View style={styles.beaconInfo}>
                <Text style={styles.beaconName}>{item.beacon.name}</Text>
                <Text style={styles.beaconId} numberOfLines={2}>
                  {describeBeacon(item.beacon)}
                </Text>
                <Text style={styles.beaconRssi}>
                  {item.sighting
                    ? `RSSI ${item.sighting.rssi} dBm, ${Math.max(0, Math.round((now - item.sighting.seenAt) / 1000))}s ago`
                    : "No signal in the last 15 seconds"}
                </Text>
              </View>
              <View style={[styles.badge, { backgroundColor: style.background }]}>
                <Text style={[styles.badgeText, { color: style.color }]}>{style.label}</Text>
              </View>
            </View>
          );
        }}
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
    color: "#4A3B94",
    fontSize: 14,
    lineHeight: 20,
  },
  warningBox: {
    backgroundColor: "#FFF0CF",
    borderRadius: 8,
    padding: 12,
    marginBottom: 16,
  },
  warningText: {
    color: "#6B4500",
    fontSize: 14,
    lineHeight: 20,
  },
  retryButton: {
    alignSelf: "flex-start",
    marginTop: 8,
    backgroundColor: "#6A5ACD",
    borderRadius: 6,
    paddingVertical: 6,
    paddingHorizontal: 14,
  },
  retryText: {
    color: "#fff",
    fontWeight: "600",
  },
  emptyText: {
    color: "#4A3B94",
    fontSize: 15,
    textAlign: "center",
    marginTop: 40,
  },
  beaconContainer: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "#EDEAFF",
    padding: 12,
    borderRadius: 8,
    marginBottom: 10,
    gap: 12,
  },
  beaconInfo: {
    flex: 1,
  },
  beaconName: {
    fontSize: 16,
    fontWeight: "bold",
    color: "#4A3B94",
  },
  beaconId: {
    fontSize: 11,
    color: "#6A5ACD",
    marginTop: 2,
  },
  beaconRssi: {
    fontSize: 14,
    color: "#4A3B94",
    marginTop: 4,
  },
  badge: {
    borderRadius: 12,
    paddingVertical: 4,
    paddingHorizontal: 10,
  },
  badgeText: {
    fontSize: 13,
    fontWeight: "700",
  },
});

export default BluetoothScanner;
