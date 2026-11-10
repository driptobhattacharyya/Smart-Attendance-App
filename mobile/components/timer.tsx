import React, { useContext, useEffect, useRef, useState } from "react";
import { Text, View, StyleSheet, FlatList } from "react-native";
import { AppContext, Session } from "@/context/AppContext";
import { fetchMyAttendance, PresenceState } from "@/lib/attendanceApi";

const pad = (n: number) => String(n).padStart(2, "0");
const todayLocal = () => {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

const formatTime = (seconds: number) =>
  `${pad(Math.floor(seconds / 3600))}:${pad(Math.floor((seconds % 3600) / 60))}:${pad(seconds % 60)}`;

const formatMinutes = (m: number) => `${Math.floor(m / 60)}h ${pad(m % 60)}m`;

const CHIP: Record<PresenceState, { label: string; color: string; background: string }> = {
  present: { label: "At your desk", color: "#1B7F3B", background: "#DDF5E4" },
  away: { label: "Away from desk – time paused", color: "#9A6200", background: "#FFF0CF" },
  outside: { label: "Outside office", color: "#B3261E", background: "#FADBD8" },
};

const sessionDetail = (s: Session) =>
  s.source === "request"
    ? `Approved request · ${formatMinutes(s.minutes)}`
    : `Present ${formatMinutes(s.present_minutes ?? s.minutes)} · Away ${formatMinutes(s.away_minutes ?? 0)}`;

/** Today's attendance as recorded by the server, ticking live while the phone is at the desk. */
const Timer = () => {
  const { isInsideGeofence, isCheckedIn, openSession, todayMinutes, presence } = useContext(AppContext);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [now, setNow] = useState(Date.now());
  // When the worked minutes were last confirmed by the server; the clock ticks on from there.
  const syncedAt = useRef(Date.now());

  useEffect(() => {
    syncedAt.current = Date.now();
    setNow(Date.now());
  }, [todayMinutes, presence, openSession?.id]);

  // Reload today's sessions whenever the check-in state or worked time changes.
  useEffect(() => {
    const today = todayLocal();
    fetchMyAttendance({ start: today, end: today })
      .then((report) => setSessions((report.days[0]?.sessions ?? []) as Session[]))
      .catch((error) => console.warn("Could not load today's sessions", error));
  }, [openSession?.id, todayMinutes, presence]);

  // Only the time at the desk counts, so the clock pauses while away or outside.
  const ticking = isCheckedIn && (presence === "present" || presence === null);
  useEffect(() => {
    if (!ticking) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [ticking]);

  const totalSeconds = todayMinutes * 60 + (ticking ? Math.max(0, Math.floor((now - syncedAt.current) / 1000)) : 0);

  if (!sessions.length && !isCheckedIn) {
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

  const chip = isCheckedIn && presence ? CHIP[presence] : null;

  return (
    <View style={styles.container}>
      <View style={styles.timerCircle}>
        <Text style={styles.timerText}>{formatTime(totalSeconds)}</Text>
        <Text style={styles.timerCaption}>worked today</Text>
      </View>

      {chip ? (
        <View style={[styles.chip, { backgroundColor: chip.background }]}>
          <Text style={[styles.chipText, { color: chip.color }]}>{chip.label}</Text>
        </View>
      ) : (
        <Text style={styles.statusText}>
          {isCheckedIn
            ? "Checked in. Timer is running..."
            : `Checked out.\n${
                isInsideGeofence
                  ? "Scan your face to check back in."
                  : "You are outside the office area."
              }`}
        </Text>
      )}

      <View style={styles.tableContainer}>
        <Text style={styles.tableHeader}>Today's sessions</Text>
        <FlatList
          data={sessions}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <View style={styles.tableRow}>
              <Text style={styles.tableCell}>
                {new Date(item.started_at).toLocaleTimeString()} {"→"}{" "}
                {item.ended_at ? new Date(item.ended_at).toLocaleTimeString() : "Ongoing"}
              </Text>
              <Text style={styles.tableSub}>{sessionDetail(item)}</Text>
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
  timerCaption: {
    fontSize: 14,
    color: "#5B3B8E",
    marginTop: 4,
  },
  chip: {
    borderRadius: 20,
    paddingVertical: 8,
    paddingHorizontal: 18,
    marginBottom: 20,
  },
  chipText: {
    fontSize: 16,
    fontWeight: "700",
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
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#D9C2F0",
  },
  tableCell: {
    fontSize: 16,
    color: "#333",
  },
  tableSub: {
    fontSize: 13,
    color: "#6A4CAF",
    marginTop: 2,
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
