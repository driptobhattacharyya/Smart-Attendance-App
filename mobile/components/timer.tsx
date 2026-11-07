import React, { useContext, useEffect, useState } from "react";
import { Text, View, StyleSheet, FlatList } from "react-native";
import { AppContext, Session } from "@/context/AppContext";
import { apiFetch } from "@/lib/api";
import { API_CONFIG } from "@/lib/config";

const pad = (n: number) => String(n).padStart(2, "0");
const todayLocal = () => {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

const formatTime = (seconds: number) =>
  `${pad(Math.floor(seconds / 3600))}:${pad(Math.floor((seconds % 3600) / 60))}:${pad(seconds % 60)}`;

/** Today's attendance as recorded by the server, ticking live while checked in. */
const Timer = () => {
  const { isInsideGeofence, isCheckedIn, openSession, todayMinutes } = useContext(AppContext);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [now, setNow] = useState(Date.now());

  // Reload today's sessions whenever the check-in state changes.
  useEffect(() => {
    const today = todayLocal();
    apiFetch(`${API_CONFIG.ENDPOINTS.GET_ATTENDANCE}?start=${today}&end=${today}`)
      .then((report) => setSessions(report.days[0]?.sessions ?? []))
      .catch((error) => console.warn("Could not load today's sessions", error));
  }, [openSession?.id, todayMinutes]);

  useEffect(() => {
    if (!isCheckedIn) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [isCheckedIn]);

  const totalSeconds = sessions.reduce((sum, s) => {
    if (s.ended_at) return sum + s.minutes * 60;
    return sum + Math.max(0, Math.floor((now - new Date(s.started_at).getTime()) / 1000));
  }, 0);

  if (!sessions.length) {
    return (
      <View style={styles.emptyContainer}>
        <Text style={styles.motivationText}>
          {isInsideGeofence
            ? "You're at the office. Scan your face to check in."
            : "Hurry, get to your office now!"}
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.timerCircle}>
        <Text style={styles.timerText}>{formatTime(totalSeconds)}</Text>
      </View>

      <Text style={styles.statusText}>
        {isCheckedIn
          ? "Checked in. Timer is running..."
          : `Checked out.\n${
              isInsideGeofence
                ? "Scan your face to check back in."
                : "You are outside the office area."
            }`}
      </Text>

      <View style={styles.tableContainer}>
        <Text style={styles.tableHeader}>Today's sessions</Text>
        <FlatList
          data={sessions}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <View style={styles.tableRow}>
              <Text style={styles.tableCell}>
                In: {new Date(item.started_at).toLocaleTimeString()}
              </Text>
              <Text style={styles.tableCell}>
                Out: {item.ended_at ? new Date(item.ended_at).toLocaleTimeString() : "Ongoing"}
              </Text>
            </View>
          )}
        />
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: 20,
    backgroundColor: "#F4E9FD", // Light purple background
  },
  timerCircle: {
    width: 200,
    height: 200,
    borderRadius: 100,
    backgroundColor: "#D3BDF2", // Circle color
    justifyContent: "center",
    alignItems: "center",
    marginBottom: 20,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 5,
    elevation: 5,
  },
  timerText: {
    fontSize: 32,
    fontWeight: "bold",
    color: "#5B3B8E", // Dark purple text
  },
  statusText: {
    fontSize: 18,
    color: "#6A4CAF",
    marginBottom: 20,
  },
  tableContainer: {
    width: "100%",
    marginTop: 10,
  },
  tableHeader: {
    fontSize: 18,
    fontWeight: "bold",
    color: "#5B3B8E",
    marginBottom: 10,
    textAlign: "center",
  },
  tableRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#D9C2F0",
  },
  tableCell: {
    fontSize: 16,
    color: "#333",
  },
  emptyContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#F4E9FD",
  },
  motivationText: {
    fontSize: 20,
    color: "#7C4DFF",
    fontWeight: "bold",
  },
});

export default Timer;
