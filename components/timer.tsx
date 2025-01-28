import React, { useEffect, useState, useContext } from "react";
import { Text, View, StyleSheet, FlatList } from "react-native";
import { AppContext } from "../app/AppContext";

const Timer = () => {
  const {
    isInsideGeofence,
    isFaceScanned,
    entryTimes,
    exitTimes,
    setEntryTimes,
    setExitTimes,
    accumulatedTime,
    setAccumulatedTime,
    isInBluetoothRange,
  } = useContext(AppContext);
  
  const [isTimerRunning, setIsTimerRunning] = useState(false);

  useEffect(() => {
    let timer;

    // Start or stop timer based on geofence status
    if (isInsideGeofence && isFaceScanned && isInBluetoothRange) {
      setIsTimerRunning(true);
      const lastEntryTime = new Date(
        entryTimes[entryTimes.length - 1] || new Date()
      );
      timer = setInterval(() => {
        setAccumulatedTime((prevTime) => prevTime + 1); // Increment by 1 second
      }, 1000); // Update every second
    } else {
      setIsTimerRunning(false);
      clearInterval(timer);
    }

    return () => clearInterval(timer);
  }, [isInsideGeofence, entryTimes, isInBluetoothRange]);

  const saveAttendanceData = async () => {
    try {
      // Hardcoded employee_id and data formatting
      const employeeId = "EMP003";
      const totalMinutes = Math.floor(accumulatedTime / 60); // Convert seconds to minutes
      const today = new Date().toISOString().split("T")[0]; // Format as YYYY-MM-DD

      const payload = {
        employee_id: employeeId,
        total_time: totalMinutes,
        entry_times: entryTimes,
        exit_times: exitTimes,
        date: today,
      };

      // API endpoint URL
      const url =
        "https://idx-backendattendance-1890784-k27k4ovjaq-ue.a.run.app/api/user/mark_attendance"; // Replace with actual endpoint

      // Fetch POST request
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });
      
      if (response.ok) {
        const data = await response.json();
        console.log("Attendance saved successfully:", data);

        // Reset the state variables
        setEntryTimes([]);
        setExitTimes([]);
        setAccumulatedTime(0);
        setIsTimerRunning(false);
        console.log("Timer reset for the next day.");
      } else {
        console.error("Failed to save attendance:", response.statusText);
      }
    } catch (error) {
      console.error("Error saving attendance data:", error);
    }
  };

  useEffect(() => {
    // Automate API call at a specific time
    const scheduleTime = new Date();
    scheduleTime.setHours(18, 56, 0); // Set to 11:59 PM (or your preferred time)

    const now = new Date();
    const delay = scheduleTime - now;

    if (delay > 0) {
      const timeoutId = setTimeout(() => {
        saveAttendanceData();
      }, delay);

      return () => clearTimeout(timeoutId); // Cleanup timeout if component unmounts
    }
  }, [entryTimes, exitTimes, accumulatedTime]);

  // Convert accumulated seconds to HH:MM:SS
  const formatTime = (seconds) => {
    const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
    const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
    const secs = String(seconds % 60).padStart(2, "0");
    return `${hours}:${minutes}:${secs}`;
  };

  // Handle empty state
  if (!entryTimes.length) {
    return (
      <View style={styles.emptyContainer}>
        <Text style={styles.motivationText}>
          Hurry, get to your office now!
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {/* Timer Circle */}
      <View style={styles.timerCircle}>
        <Text style={styles.timerText}>{formatTime(accumulatedTime)}</Text>
      </View>

      {/* Geofence Status */}
      <Text style={styles.statusText}>
  {isTimerRunning
    ? "Timer is running..."
    : `Timer is paused.\n${
        !isInsideGeofence
          ? "Outside Geofence."
          : !isInBluetoothRange
          ? "Outside Bluetooth Range."
          : "Some problem has occurred. Please try again later."
      }`}
</Text>
      


      {/* Entry and Exit Times Table */}
      <View style={styles.tableContainer}>
        <Text style={styles.tableHeader}>Entry and Exit Times</Text>
        <FlatList
          data={entryTimes}
          keyExtractor={(item, index) => index.toString()}
          renderItem={({ item, index }) => (
            <View style={styles.tableRow}>
              <Text style={styles.tableCell}>
                Entry: {new Date(item).toLocaleTimeString()}
              </Text>
              <Text style={styles.tableCell}>
                Exit:{" "}
                {exitTimes[index]
                  ? new Date(exitTimes[index]).toLocaleTimeString()
                  : "Ongoing"}
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
