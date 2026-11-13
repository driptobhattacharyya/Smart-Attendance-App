import React, { useContext, useMemo, useState } from "react";
import {
  View,
  Text,
  TextInput,
  Button,
  Modal,
  TouchableOpacity,
  StyleSheet,
} from "react-native";
import DateTimePicker from "@react-native-community/datetimepicker";
import { Alert } from "react-native";
import { AppContext } from "@/context/AppContext";
import { errorMessage } from "@/lib/errors";
import { ModuleKey, isModuleOn } from "@/lib/profile";
import { RequestBody, RequestKind, submitRequest } from "@/lib/attendanceApi";

/** What the employee can ask for. Each reason maps to a request kind and needs its module. */
const REASONS: { label: string; kind: RequestKind; module: ModuleKey }[] = [
  { label: "Work from home", kind: "wfh", module: "wfh" },
  { label: "Off-Campus duty", kind: "correction", module: "regularization" },
  { label: "Forgot to check-in", kind: "correction", module: "regularization" },
  { label: "Leave", kind: "leave", module: "leave" },
  { label: "Other", kind: "correction", module: "regularization" },
];

// YYYY-MM-DD and HH:MM in the phone's own timezone (toISOString would use UTC).
const pad = (n: number) => String(n).padStart(2, "0");
const localDate = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
import * as DocumentPicker from "expo-document-picker";
import { Picker } from "@react-native-picker/picker";
import { Ionicons } from "@expo/vector-icons"; // Import icons

type RequestFormProps = {
  isVisible: boolean;
  onClose: () => void;
  onSubmit?: (body: RequestBody) => void;
};

const RequestForm = ({ isVisible, onClose, onSubmit }: RequestFormProps) => {
  const { modules } = useContext(AppContext);
  const reasons = useMemo(() => REASONS.filter((r) => isModuleOn(modules, r.module)), [modules]);
  const [reason, setReason] = useState("");
  const [entryTime, setEntryTime] = useState(new Date());
  const [exitTime, setExitTime] = useState(new Date());
  const [startDate, setStartDate] = useState(new Date());
  const [endDate, setEndDate] = useState(new Date());
  const [showEntryPicker, setShowEntryPicker] = useState(false);
  const [showExitPicker, setShowExitPicker] = useState(false);
  const [showStartDatePicker, setShowStartDatePicker] = useState(false);
  const [showEndDatePicker, setShowEndDatePicker] = useState(false);
  const [selectedFile, setSelectedFile] = useState<DocumentPicker.DocumentPickerAsset | null>(null);
  const [isMultipleDays, setIsMultipleDays] = useState(false); // Single or Multiple Days
  const [isOtherReason, setIsOtherReason] = useState(false); // Other reason toggle
  const [description, setDescription] = useState("");
  const [otherText, setOtherText] = useState("");

  const handleTimeChange = (type: "entry" | "exit", event: unknown, selectedTime?: Date) => {
    if (selectedTime) {
      if (type === "entry") {
        setEntryTime(selectedTime);
      } else {
        setExitTime(selectedTime);
      }
    }
    setShowEntryPicker(false);
    setShowExitPicker(false);
  };

  const handleDateChange = (type: "start" | "end", event: unknown, selectedDate?: Date) => {
    if (selectedDate) {
      if (type === "start") {
        setStartDate(selectedDate);
      } else {
        setEndDate(selectedDate);
      }
    }
    setShowStartDatePicker(false);
    setShowEndDatePicker(false);
  };

  const handleFileUpload = async () => {
    const result = await DocumentPicker.getDocumentAsync({
      type: "*/*",
      copyToCacheDirectory: true,
    });

    if (!result.canceled && result.assets?.length) {
      setSelectedFile(result.assets[0]);
    }
  };

  const handleSubmit = async () => {
    const chosen = isOtherReason ? "Other" : reason || reasons[0]?.label;
    const kind = reasons.find((r) => r.label === chosen)?.kind;
    if (!kind) {
      Alert.alert("Not available", "Your company has not enabled requests.");
      return;
    }
    const finalReason = isOtherReason ? otherText.trim() : chosen;
    if (!finalReason || finalReason.length < 3) {
      Alert.alert("Missing reason", "Please choose or describe a reason.");
      return;
    }
    const start = localDate(startDate);
    const end = isMultipleDays ? localDate(endDate) : start;
    const body: RequestBody = {
      kind,
      reason: description && !isOtherReason ? `${finalReason}: ${description}` : finalReason,
      start_date: start,
      end_date: end,
    };
    if (!isMultipleDays && kind === "correction") {
      // A single day with times becomes an attendance correction once approved.
      body.entry_time = localTime(entryTime);
      body.exit_time = localTime(exitTime);
    }
    try {
      await submitRequest(body);
      Alert.alert("Sent", "Your request was sent to your manager.");
      onSubmit?.(body);
      onClose();
    } catch (error: any) {
      Alert.alert("Could not send request", errorMessage(error, "Please try again."));
    }
  };

  return (
    <Modal visible={isVisible} animationType="slide" transparent>
      <View style={styles.container}>
        <Text style={styles.headerText}>Request Attendance</Text>

        {/* Reason Dropdown */}
        <Text style={styles.label}>Reason for absence:</Text>
        <Picker
          selectedValue={reason || reasons[0]?.label}
          style={styles.picker}
          onValueChange={(itemValue) => {
            setReason(itemValue);
            setIsOtherReason(itemValue === "Other");
          }}
        >
          {reasons.map((r) => (
            <Picker.Item key={r.label} label={r.label} value={r.label} />
          ))}
        </Picker>

        {/* Show Text Input if "Other" is selected */}
        {isOtherReason && (
          <TextInput
            placeholder="Please specify"
            style={styles.input}
            value={otherText}
            onChangeText={setOtherText}
          />
        )}

        {/* Single / Multiple Days Toggle */}
        <Text style={styles.label}>Select Days:</Text>
        <View style={styles.radioGroup}>
          <TouchableOpacity
            onPress={() => setIsMultipleDays(false)}
            style={[
              styles.radioButton,
              !isMultipleDays && styles.selectedButton,
            ]}
          >
            <Text style={styles.radioText}>Single Day</Text>
          </TouchableOpacity>
          <TouchableOpacity
            onPress={() => setIsMultipleDays(true)}
            style={[
              styles.radioButton,
              isMultipleDays && styles.selectedButton,
            ]}
          >
            <Text style={styles.radioText}>Multiple Days</Text>
          </TouchableOpacity>
        </View>

        {/* Date Range or Single Date Picker */}
        <View style={styles.dateRow}>
          {isMultipleDays ? (
            <View style={{ flexDirection: "column" }}>
              <TouchableOpacity
                onPress={() => setShowStartDatePicker(true)}
                style={styles.datePickerButton}
              >
                <Text style={styles.dateText}>Start </Text>
                <Ionicons name="calendar" size={24} color="#6A4CAF" />
                <Text style={styles.dateText}>
                  : {startDate.toDateString()}
                </Text>
              </TouchableOpacity>
              {showStartDatePicker && (
                <DateTimePicker
                  value={startDate}
                  mode="date"
                  display="default"
                  onChange={(event, selectedDate) =>
                    handleDateChange("start", event, selectedDate)
                  }
                />
              )}
              <TouchableOpacity
                onPress={() => setShowEndDatePicker(true)}
                style={styles.datePickerButton}
              >
                <Text style={styles.dateText}> End </Text>
                <Ionicons name="calendar" size={24} color="#6A4CAF" />
                <Text style={styles.dateText}>: {endDate.toDateString()}</Text>
              </TouchableOpacity>
              {showEndDatePicker && (
                <DateTimePicker
                  value={endDate}
                  mode="date"
                  display="default"
                  onChange={(event, selectedDate) =>
                    handleDateChange("end", event, selectedDate)
                  }
                />
              )}
            </View>
          ) : (
            <>
              <TouchableOpacity
                onPress={() => setShowStartDatePicker(true)}
                style={styles.datePickerButton}
              >
                <Ionicons name="calendar" size={24} color="#6A4CAF" />
                <Text style={styles.dateText}>{startDate.toDateString()}</Text>
              </TouchableOpacity>
              {showStartDatePicker && (
                <DateTimePicker
                  value={startDate}
                  mode="date"
                  display="default"
                  onChange={(event, selectedDate) =>
                    handleDateChange("start", event, selectedDate)
                  }
                />
              )}
            </>
          )}
        </View>

        {/* Entry and Exit Time Pickers */}
        <View style={styles.dateRow}>
          <TouchableOpacity
            onPress={() => setShowEntryPicker(true)}
            style={styles.timePickerButton}
          >
            <Text style={styles.timeText}>Start </Text>
            <Ionicons name="time" size={24} color="#6A4CAF" />
            <Text style={styles.timeText}>
              : {entryTime.toLocaleTimeString()}
            </Text>
          </TouchableOpacity>
        </View>

        <View style={styles.dateRow}>
          <TouchableOpacity
            onPress={() => setShowExitPicker(true)}
            style={styles.timePickerButton}
          >
            <Text style={styles.timeText}>End </Text>
            <Ionicons name="time" size={24} color="#6A4CAF" />
            <Text style={styles.timeText}>
              : {exitTime.toLocaleTimeString()}
            </Text>
          </TouchableOpacity>
        </View>

        {showEntryPicker && (
          <DateTimePicker
            value={entryTime}
            mode="time"
            display="default"
            onChange={(event, selectedTime) =>
              handleTimeChange("entry", event, selectedTime)
            }
          />
        )}

        {showExitPicker && (
          <DateTimePicker
            value={exitTime}
            mode="time"
            display="default"
            onChange={(event, selectedTime) =>
              handleTimeChange("exit", event, selectedTime)
            }
          />
        )}

        {/* Free Text Description (Optional) */}
        <TextInput
          placeholder="Provide additional details (optional)"
          style={styles.input}
          value={description}
          onChangeText={setDescription}
          multiline
        />

        {/* File Upload */}
        <TouchableOpacity
          onPress={handleFileUpload}
          style={styles.uploadButton}
        >
          <Text style={styles.uploadButtonText}>
            {selectedFile ? selectedFile.name : "Upload Document"}
          </Text>
        </TouchableOpacity>

        {/* Action Buttons */}
        <View style={styles.buttonRow}>
          <Button title="Submit" onPress={handleSubmit} />
          <Button title="Cancel" onPress={onClose} color="red" />
        </View>
      </View>
    </Modal>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#F4E9FD",
    padding: 20,
  },
  headerText: {
    fontSize: 24,
    fontWeight: "bold",
    color: "#5B3B8E",
    marginBottom: 20,
  },
  input: {
    width: "100%",
    padding: 10,
    borderWidth: 1,
    borderColor: "#D9C2F0",
    borderRadius: 5,
    marginBottom: 10,
    backgroundColor: "#fff",
  },
  picker: {
    width: "100%",
    padding: 10,
    borderWidth: 1,
    borderColor: "#D9C2F0",
    borderRadius: 5,
    marginBottom: 10,
  },
  label: {
    fontSize: 16,
    color: "#5B3B8E",
    marginBottom: 5,
  },
  radioGroup: {
    flexDirection: "row",
    marginBottom: 10,
  },
  radioButton: {
    padding: 10,
    borderRadius: 5,
    backgroundColor: "#D3BDF2",
    marginRight: 10,
  },
  selectedButton: {
    backgroundColor: "#6A4CAF",
  },
  radioText: {
    color: "#5B3B8E",
    fontSize: 16,
  },
  dateRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 10,
  },
  datePickerButton: {
    flexDirection: "row",
    alignItems: "center",
  },
  timePickerButton: {
    flexDirection: "row",
    alignItems: "center",
  },
  dateText: {
    fontSize: 16,
    color: "#6A4CAF",
    marginLeft: 5,
  },
  timeText: {
    fontSize: 16,
    color: "#6A4CAF",
    marginLeft: 5,
  },
  uploadButton: {
    backgroundColor: "#D3BDF2",
    padding: 10,
    borderRadius: 5,
    marginVertical: 10,
  },
  uploadButtonText: {
    fontSize: 16,
    color: "#5B3B8E",
  },
  buttonRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    width: "100%",
    marginTop: 20,
  },
});

export default RequestForm;
