import React, { useContext, useState } from "react";
import { ActivityIndicator, Linking, Platform, ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { AppContext } from "@/context/AppContext";
import { requestBackgroundLocation, requestForegroundLocation } from "@/lib/presence";

type Step = "foreground" | "bluetooth" | "background";

const COPY: Record<Step, { title: string; body: string; button: string }> = {
  foreground: {
    title: "Allow location",
    body:
      "Attendify uses your location to know when you arrive at the office and whether you are inside it. " +
      "Your location is only used for attendance.",
    button: "Allow location",
  },
  bluetooth: {
    title: "Allow Bluetooth",
    body:
      "Your admin placed a Bluetooth beacon at your desk. Attendify listens for it to confirm that you are at " +
      "your desk, so time away from it is not counted as work. It does not connect to or track any other device.",
    button: "Allow Bluetooth",
  },
  background: {
    title: "Keep tracking in the background",
    body:
      "While you are checked in, Attendify reports your presence every few minutes, even when the app is closed " +
      "or the screen is off. Without this your work time stops counting as soon as you leave the app.\n\n" +
      (Platform.OS === "android"
        ? 'A notification "Attendify is tracking your work session" is shown while it runs. On the next screen ' +
          'choose "Allow all the time" for Location.'
        : 'iOS will ask you to keep allowing location access "Always". A blue status bar indicator shows while it runs.'),
    button: "Continue",
  },
};

/**
 * Explains why each permission is needed before asking for it. Background location is
 * only requested after foreground location has been granted. Renders its children when
 * there is nothing left to ask (or the user chose "Not now").
 */
export default function PermissionGate({ children }: { children: React.ReactNode }) {
  const { locationPermissions, refreshLocationPermissions, beacons, bleStatus, enableScanning } = useContext(AppContext);
  const [dismissed, setDismissed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deniedStep, setDeniedStep] = useState<Step | null>(null);

  let step: Step | null = null;
  if (!locationPermissions.foreground) step = "foreground";
  else if (beacons.length > 0 && bleStatus === "no-permission") step = "bluetooth";
  else if (!locationPermissions.background) step = "background";

  if (!step || dismissed) return <>{children}</>;

  const copy = COPY[step];

  const onAllow = async () => {
    setBusy(true);
    setDeniedStep(null);
    try {
      if (step === "foreground") {
        if (!(await requestForegroundLocation())) setDeniedStep(step);
      } else if (step === "bluetooth") {
        await enableScanning();
      } else {
        if (!(await requestBackgroundLocation())) setDeniedStep(step);
      }
      await refreshLocationPermissions();
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>{copy.title}</Text>
      <Text style={styles.body}>{copy.body}</Text>
      {deniedStep === step && (
        <View style={styles.denied}>
          <Text style={styles.deniedText}>The permission was not granted. You can change it in the system settings.</Text>
          <TouchableOpacity onPress={() => Linking.openSettings()}>
            <Text style={styles.link}>Open settings</Text>
          </TouchableOpacity>
        </View>
      )}
      <TouchableOpacity style={[styles.button, busy && styles.buttonDisabled]} onPress={onAllow} disabled={busy}>
        {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>{copy.button}</Text>}
      </TouchableOpacity>
      <TouchableOpacity onPress={() => setDismissed(true)} style={styles.skip}>
        <Text style={styles.link}>Not now</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    justifyContent: "center",
    padding: 24,
    backgroundColor: "#F4E9FD",
  },
  title: { fontSize: 26, fontWeight: "bold", color: "#5B3B8E", marginBottom: 16 },
  body: { fontSize: 16, lineHeight: 24, color: "#4A3B94", marginBottom: 24 },
  button: {
    backgroundColor: "#6A4CAF",
    borderRadius: 8,
    height: 50,
    alignItems: "center",
    justifyContent: "center",
  },
  buttonDisabled: { opacity: 0.7 },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "bold" },
  skip: { alignItems: "center", padding: 16 },
  link: { color: "#6A4CAF", fontSize: 16, textDecorationLine: "underline" },
  denied: { backgroundColor: "#FFF0CF", borderRadius: 8, padding: 12, marginBottom: 16 },
  deniedText: { color: "#6B4500", marginBottom: 6 },
});
