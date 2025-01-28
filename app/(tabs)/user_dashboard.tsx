import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  Alert,
  ActivityIndicator,
} from "react-native";
import { BarChart } from "react-native-gifted-charts";
import { useNavigation } from "@react-navigation/native";
import * as SecureStore from "expo-secure-store";
import { useRouter } from "expo-router";
import { format } from "date-fns";
import RequestForm from "@/components/request_form";

const UserDashboard = () => {
  const navigation = useNavigation();
  const router = useRouter();
  const [name, setName] = useState("");
  const [attendanceData, setAttendanceData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState({
    totalHours: 0,
    averageDailyTime: "0h",
    daysPresent: 0,
  });
  const [isModalVisible, setIsModalVisible] = useState(false);

  const handleFormSubmit = (formData) => {
    console.log("Form Submitted:", formData);
    setIsModalVisible(false);
    // You can make the API call to submit the form data here
    // Example: await axios.post('/api/attendance-requests', formData);
  };

  useEffect(() => {
    const fetchAttendanceData = async () => {
      try {
        const employeeId = await SecureStore.getItemAsync("employee_id");
        console.log("Employee ID from session:", employeeId);
        if (!employeeId) {
          Alert.alert("Error", "No employee ID found. Please log in again");
          router.replace("/");
          return;
        }

        const response = await fetch(
          `https://idx-backendattendance-1890784-k27k4ovjaq-ue.a.run.app/api/user/get_attendance/${employeeId}`,
          {
            method: "GET",
            headers: {
              "Content-Type": "application/json",
            },
          }
        );
        if (!response.ok) {
          throw new Error(
            `Failed to fetch attendance data. Status: ${response.status}`
          );
        }
        const data = await response.json();
        if (!data.attendance || !Array.isArray(data.attendance)) {
          throw new Error("Invalid attendance data from API.");
        }

        console.log("Response data", data);
        setAttendanceData(data.attendance);

        const totalHours = data.attendance.reduce(
          (sum, record) => sum + (record.total_time || 0) / 60,
          0
        ); // Convert minutes to hours
        const daysPresent = data.attendance.length;
        const averageDailyTime = (totalHours / daysPresent).toFixed(2);

        setSummary({
          totalHours: totalHours.toFixed(2), // Round to 2 decimals
          averageDailyTime: `${averageDailyTime}h`,
          daysPresent,
        });

        setName(data.employee_name);
        setLoading(false);
      } catch (error) {
        console.error("Error fetching attendance data:", error);
        Alert.alert("Error", "Failed to load attendance data.");
        setLoading(false);
      }
    };

    fetchAttendanceData();
  }, []);

  const handleLogout = () => {
    Alert.alert("Log Out", "Are you sure you want to log out?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Log Out",
        onPress: async () => {
          await SecureStore.deleteItemAsync("employee_id");
          console.log("employee_id cleared from session");
          router.replace("/");
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

  const Labels = ({ x, y, bandwidth, data }) => {
    return data.map((value, index) => (
      <Text
        key={index}
        style={{
          position: "absolute",
          left: x(index) + bandwidth / 2 - 10,
          top: y(value) - 20,
          fontSize: 12,
          color: "#442c91",
        }}
      >
        {value}h
      </Text>
    ));
  };

  const data =
    attendanceData?.map((record) => {
      const totalTime = Math.ceil(parseFloat(record.total_time / 60)); // Convert minutes to hours
      return {
        value: isNaN(totalTime) ? 0 : totalTime, // Set `value` as required
        label: format(new Date(record.date), "dd/MM"),
        frontColor: "#cbb8fc",
      };
    }) || [];
  console.log(
    "BarChart data:",
    data.map((item) => item.value)
  );
  

  return (
    <ScrollView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.greeting}>
          Hello, {name ? ` ${name}` : "User"}!
        </Text>
        <TouchableOpacity onPress={handleLogout} style={styles.logoutButton}>
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
          style={{ height: 200, marginBottom: 10 }}
          data={data} // Pass only the values to the chart
          // contentInset={{ top: 30, bottom: 10 }}
          spacingInner={0.4} // Adjust space between bars
          showGradient={true}
          gradientColor={"#442c91"}
          noOfSections={4}
          xAxisLabelTextStyle={styles.xAxisText}
          yAxisLabelTextStyle={styles.xAxisText}
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
                    {format(new Date(time), "HH:mm")}{" "}
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
  attendanceList: {
    marginBottom: 20,
  },
  listTitle: {
    fontSize: 18,
    fontWeight: "bold",
    color: "#442c91",
    marginBottom: 10,
  },
  recordItem: {
    flexDirection: "row",
    justifyContent: "space-between",
    backgroundColor: "#fdf9ff",
    padding: 10,
    borderRadius: 5,
    marginBottom: 10,
  },
  recordText: {
    fontSize: 14,
    color: "#442c91",
  },
  lateStatus: {
    color: "#e63946",
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
});

export default UserDashboard;
