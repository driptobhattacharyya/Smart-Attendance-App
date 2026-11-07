import React, { useContext, useRef, useState } from "react";
import { ActivityIndicator, Alert, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import FaceRegistration from "@/components/FaceRegistration";
import { AppContext } from "@/context/AppContext";
import { enrolFace } from "@/lib/api";

/**
 * Shown after consent when the account has no enrolled face yet (for example an account
 * created while consent was pending). Reuses the signup camera step.
 */
export default function FaceEnrolmentScreen() {
  const { setFaceEnrolled, handleLogout } = useContext(AppContext);
  const [busy, setBusy] = useState(false);
  // FaceRegistration reports the photo and then calls onNext, before any state update
  // from setFormData would be visible, so the photo is kept in a ref.
  const photoRef = useRef<{ uri: string } | null>(null);

  const submit = async () => {
    const photo = photoRef.current;
    if (!photo) return;
    setBusy(true);
    try {
      await enrolFace(photo);
      setFaceEnrolled(true);
    } catch (error: any) {
      Alert.alert("Error", error?.message || "Face registration failed. Please try again.");
      photoRef.current = null;
    } finally {
      setBusy(false);
    }
  };

  if (busy) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.center}>
          <ActivityIndicator size="large" color="#442c91" />
          <Text style={styles.text}>Saving your face photo...</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={["top", "bottom"]}>
      <FaceRegistration
        formData={{}}
        setFormData={(data: any) => {
          photoRef.current = data?.photo ?? null;
        }}
        onNext={submit}
      />
      <TouchableOpacity style={styles.logout} onPress={() => handleLogout()}>
        <Text style={styles.logoutText}>Log out</Text>
      </TouchableOpacity>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f4eefe" },
  center: { flex: 1, justifyContent: "center", alignItems: "center", gap: 12 },
  text: { fontSize: 16, color: "#442c91" },
  logout: { alignItems: "center", padding: 12 },
  logoutText: { color: "#6A4CAF", fontSize: 16, textDecorationLine: "underline" },
});
