import React, { useEffect, useState, useContext } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  Alert,
  ActivityIndicator,
  Modal,
} from "react-native";
import { BarChart } from "react-native-gifted-charts";
import * as SecureStore from "expo-secure-store";
import * as FileSystem from "expo-file-system/legacy";
import { useRouter } from "expo-router";
import { format } from "date-fns";
import RequestForm from "@/components/request_form";
import ConsentScreen from "@/components/ConsentScreen";
import { API_CONFIG } from "@/lib/config";
import { apiFetch } from "@/lib/api";
import { stopBackgroundTracking } from "@/lib/presence";
import { AppContext } from "@/context/AppContext";

interface AttendanceRecord {
  date: string;
  total_time: number; // minutes
  entry_times: string[];
  exit_times: (string | null)[]; // null while still checked in
}

interface Summary {
  totalHours: number;
  averageDailyTime: string;
  daysPresent: number;
}

const UserDashboard = () => {
  const router = useRouter();
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('UserDashboard must be used within an AppProvider');
  }
  const { handleLogout, setConsentRequired, refreshStatus } = context;
  const [name, setName] = useState("");
  const [attendanceData, setAttendanceData] = useState<AttendanceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<Summary>({
    totalHours: 0,
    averageDailyTime: "0h",
    daysPresent: 0,
  });
  const [isModalVisible, setIsModalVisible] = useState(false);
  const [isNoticeVisible, setIsNoticeVisible] = useState(false);
  const [exporting, setExporting] = useState(false);

  const handleFormSubmit = () => {
    setIsModalVisible(false);
  };

  /** Right of access: saves the user's data as JSON in the app's documents folder. */
  const downloadMyData = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const data = await apiFetch(API_CONFIG.ENDPOINTS.MY_EXPORT);
      const directory = FileSystem.documentDirectory;
      if (!directory) throw new Error("No documents folder on this device");
      const stamp = new Date().toISOString().slice(0, 10);
      const uri = `${directory}attendify-my-data-${stamp}.json`;
      await FileSystem.writeAsStringAsync(uri, JSON.stringify(data, null, 2));
      Alert.alert("Your data was saved", `The file was saved to:\n${uri}`);
    } catch (error) {
      console.warn("Data export failed", error);
      Alert.alert("Error", "Could not download your data. Please try again.");
    } finally {
      setExporting(false);
    }
  };

  /** Withdrawing clears consent on the server, stops background tracking and reopens the notice. */
  const withdrawConsent = () => {
    Alert.alert(
      "Withdraw consent",
      "Attendify will stop collecting your location and attendance data. If you are checked in, you will be checked out. " +
        "You will need to accept the privacy notice again to use the app.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Withdraw",
          style: "destructive",
          onPress: async () => {
            try {
              await apiFetch(API_CONFIG.ENDPOINTS.CONSENT_WITHDRAW, { method: "POST" });
            } catch (error) {
              console.warn("Consent withdrawal failed", error);
              Alert.alert("Error", "Could not withdraw consent. Check your connection and try again.");
              return;
            }
            await stopBackgroundTracking();
            await refreshStatus();
            setConsentRequired(true);
          },
        },
      ]
    );
  };

  useEffect(() => {
    const fetchAttendanceData = async () => {
      try {
        // Last 30 days, worked out by the server from check-ins and approved requests.
        const data = await apiFetch(API_CONFIG.ENDPOINTS.GET_ATTENDANCE);
        const records: AttendanceRecord[] = data.days
          .filter((day: any) => day.total_minutes > 0)
          .map((day: any) => ({
            date: day.date,
            total_time: day.total_minutes,
            entry_times: day.sessions.map((s: any) => s.started_at),
            exit_times: day.sessions.map((s: any) => s.ended_at),
          }));
        setAttendanceData(records);

        const totalHours = records.reduce(
          (sum: number, record: AttendanceRecord) => sum + (record.total_time || 0) / 60,
          0
        ); // Convert minutes to hours
        const daysPresent = records.length;
        const averageDailyTime = daysPresent ? (totalHours / daysPresent).toFixed(2) : "0.00";

        setSummary({
          totalHours: Number(totalHours.toFixed(2)), // Round to 2 decimals
          averageDailyTime: `${averageDailyTime}h`,
          daysPresent,
        });

        setName(data.name);
        setLoading(false);
      } catch (error) {
        console.error("Error fetching attendance data:", error);
        Alert.alert("Error", "Failed to load attendance data.");
        setLoading(false);
      }
    };

    fetchAttendanceData();
  }, []);

  const handleLogoutPress = async () => {
    Alert.alert("Log Out", "Are you sure you want to log out?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Log Out",
        onPress: async () => {
          try {
            await handleLogout();
          } catch (error) {
            console.error('Logout error:', error);
            Alert.alert('Error', 'Failed to log out. Please try again.');
          }
        },
      },
    ]);
  };

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#442c91" />
        <Text>Loading attendance data...</Text>
      </View>
    );
  }

  const data =
    attendanceData?.map((record) => {
      const totalTime = Math.ceil(record.total_time / 60); // Convert minutes to hours
      return {
        value: isNaN(totalTime) ? 0 : totalTime, // Set `value` as required
        label: format(new Date(record.date), "dd/MM"),
        frontColor: "#cbb8fc",
      };
    }) || [];

  return (
    <ScrollView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.greeting}>
          Hello, {name ? ` ${name}` : "User"}!
        </Text>
        <TouchableOpacity onPress={handleLogoutPress} style={styles.logoutButton}>
          <Text style={styles.logoutText}>Log Out</Text>
        </TouchableOpacity>
      </View>
      <TouchableOpacity
        style={styles.requestButton}
        onPress={() => setIsModalVisible(true)}
      >
        <Text style={styles.requestButtonText}>Request Manual Attendance</Text>
      </TouchableOpacity>
      <Text style={styles.date}>
        Today: {new Date().toISOString().split("T")[0]}
      </Text>

      {/* Uncomment this when ready to use */}
      <View style={styles.summarySection}>
        <Text style={styles.summaryText}>
          Total Hours This Month: {summary.totalHours}h
        </Text>
        <Text style={styles.summaryText}>
          Average Daily Time: {summary.averageDailyTime}
        </Text>
        <Text style={styles.summaryText}>
          Days Present: {summary.daysPresent}
        </Text>
      </View>

      <View style={styles.chartContainer}>
        <Text style={styles.chartTitle}>Weekly Attendance</Text>
        <BarChart
          height={200}
          data={data}
          spacing={16}
          showGradient={true}
          gradientColor={"#442c91"}
          noOfSections={4}
          xAxisLabelTextStyle={styles.xAxisText}
          yAxisTextStyle={styles.xAxisText}
          animationDuration={200}
        />
      </View>

      <RequestForm
        isVisible={isModalVisible}
        onClose={() => setIsModalVisible(false)}
        onSubmit={handleFormSubmit}
      />

      <View style={styles.attendanceList}>
        <Text style={styles.listTitle}>Attendance Records</Text>
        <View style={styles.headerRow}>
          <Text style={[styles.columnHeader, styles.columnDate]}>Date</Text>
          <Text style={styles.columnHeader}>Entry Times</Text>
          <Text style={styles.columnHeader}>Exit Times</Text>
          <Text style={styles.columnHeader}>Total Duration</Text>
        </View>
        {attendanceData && attendanceData.length > 0 ? (
          attendanceData.map((record, index) => (
            <View key={index} style={styles.recordItem}>
              <Text style={[styles.recordText, styles.columnDate]}>
                {record.date}
              </Text>
              <View style={styles.timeColumn}>
                {record.entry_times?.map((time, idx) => (
                  <Text key={idx} style={styles.timeText}>
                    {format(new Date(time), "HH:mm")}{" "}
                    {/* Format ISO time to HH:mm */}
                  </Text>
                )) || <Text style={styles.timeText}>N/A</Text>}
              </View>
              <View style={styles.timeColumn}>
                {record.exit_times?.map((time, idx) => (
                  <Text key={idx} style={styles.timeText}>
                    {time ? format(new Date(time), "HH:mm") : "now"}{" "}
                    {/* Format ISO time to HH:mm */}
                  </Text>
                )) || <Text style={styles.timeText}>N/A</Text>}
              </View>
              <Text style={styles.recordText}>
                {(record.total_time / 60).toFixed(2)}h
              </Text>
            </View>
          ))
        ) : (
          <Text style={styles.recordText}>
            No attendance records available.
          </Text>
        )}
      </View>

      <View style={styles.privacySection}>
        <Text style={styles.listTitle}>Privacy</Text>
        <TouchableOpacity style={styles.privacyRow} onPress={() => setIsNoticeVisible(true)}>
          <Text style={styles.privacyText}>View privacy notice</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.privacyRow} onPress={downloadMyData} disabled={exporting}>
          <Text style={styles.privacyText}>{exporting ? "Preparing your data..." : "Download my data"}</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.privacyRow} onPress={withdrawConsent}>
          <Text style={[styles.privacyText, styles.privacyDanger]}>Withdraw consent</Text>
        </TouchableOpacity>
      </View>

      <Modal visible={isNoticeVisible} animationType="slide" onRequestClose={() => setIsNoticeVisible(false)}>
        <ConsentScreen readOnly onClose={() => setIsNoticeVisible(false)} />
      </Modal>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#f4eefe",
    padding: 15,
    paddingTop: 40,
  },
  header: {
    marginBottom: 20,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  greeting: {
    fontSize: 24,
    fontWeight: "bold",
    color: "#442c91",
  },
  date: {
    fontSize: 16,
    color: "#442c91",
  },
  logoutButton: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    backgroundColor: "#e63946",
    borderRadius: 5,
  },
  logoutText: {
    color: "#ffffff",
    fontWeight: "bold",
  },
  chartContainer: {
    marginBottom: 20,
    alignItems: "center",
  },
  chartTitle: {
    fontSize: 18,
    fontWeight: "bold",
    color: "#442c91",
    marginBottom: 10,
  },
  xAxisContainer: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 10,
    width: "100%",
    paddingHorizontal: 10,
  },
  xAxisText: {
    fontSize: 12,
    color: "#442c91",
  },
  loadingContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  requestButton: {
    backgroundColor: "#442c91",
    paddingVertical: 10,
    paddingHorizontal: 20,
    marginBottom: 20,
    borderRadius: 5,
  },
  requestButtonText: {
    color: "#ffffff",
    fontSize: 16,
    fontWeight: "bold",
  },
  attendanceList: {
    marginBottom: 20,
  },
  listTitle: {
    fontSize: 18,
    fontWeight: "bold",
    color: "#442c91",
    marginBottom: 10,
  },

  // New header row styling
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    backgroundColor: "#e9d8ff",
    paddingVertical: 10,
    paddingHorizontal: 5,
    borderRadius: 5,
    marginBottom: 10,
  },
  columnHeader: {
    fontSize: 14,
    color: "#442c91",
    fontWeight: "bold",
    textAlign: "center",
    flex: 1,
  },
  columnDate: {
    flex: 1.3, // Date column takes more space
  },

  // Individual record row styling
  recordItem: {
    flexDirection: "row",
    justifyContent: "space-between",
    backgroundColor: "#fdf9ff",
    paddingVertical: 10,
    paddingHorizontal: 5,
    borderRadius: 5,
    marginBottom: 10,
  },
  recordText: {
    fontSize: 14,
    color: "#442c91",
    textAlign: "center",
    flex: 1,
  },

  // Optional late status color
  lateStatus: {
    color: "#e63946",
  },
  timeColumn: {
    flex: 1,
    alignItems: "center",
  },
  timeText: {
    fontSize: 14,
    color: "#442c91",
    textAlign: "center",
    marginVertical: 2,
  },
  summarySection: {
    marginBottom: 20,
    marginTop: 10,
    padding: 15,
    backgroundColor: "#e6e6ff",
    borderRadius: 10,
  },
  summaryText: {
    fontSize: 16,
    fontWeight: "bold",
    color: "#442c91",
    marginBottom: 5,
  },
  privacySection: {
    marginBottom: 30,
  },
  privacyRow: {
    backgroundColor: "#fdf9ff",
    paddingVertical: 12,
    paddingHorizontal: 12,
    borderRadius: 5,
    marginBottom: 10,
  },
  privacyText: {
    fontSize: 16,
    color: "#442c91",
    fontWeight: "bold",
  },
  privacyDanger: {
    color: "#e63946",
  },
});

export default UserDashboard;
