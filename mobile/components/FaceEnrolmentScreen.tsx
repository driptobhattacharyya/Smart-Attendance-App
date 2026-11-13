import React, { useContext } from "react";
import { StyleSheet, Text, TouchableOpacity } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import LivenessCapture from "@/components/LivenessCapture";
import { AppContext } from "@/context/AppContext";

/**
 * Shown after consent when the account has no enrolled face yet (every new employee starts
 * here). Takes three frames and uploads them to POST /face/enroll.
 */
export default function FaceEnrolmentScreen() {
  const { setFaceEnrolled, handleLogout } = useContext(AppContext);

  return (
    <SafeAreaView style={styles.container} edges={["top", "bottom"]}>
      <LivenessCapture mode="enroll" onSuccess={() => setFaceEnrolled(true)} />
      <TouchableOpacity style={styles.logout} onPress={() => handleLogout()}>
        <Text style={styles.logoutText}>Log out</Text>
      </TouchableOpacity>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f4eefe" },
  logout: { alignItems: "center", padding: 12 },
  logoutText: { color: "#6A4CAF", fontSize: 16, textDecorationLine: "underline" },
});
