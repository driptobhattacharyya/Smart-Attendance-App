import Timer from "@/components/timer";

import React, { useContext } from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { AppContext } from "@/context/AppContext";
import FaceRecognition from "@/components/face_recog";
import PermissionGate from "@/components/PermissionGate";

export default function App() {
  const { isInsideGeofence, isFaceScanned, isCheckedIn, wfhEnabled, remoteRequested, setRemoteRequested } =
    useContext(AppContext);

  // Ask for a face scan only when it's needed to check in (at the office, or for a remote day).
  const needsFace = !isCheckedIn && !isFaceScanned && (isInsideGeofence || remoteRequested);
  const canOfferRemote = wfhEnabled && !isCheckedIn && !isInsideGeofence && !remoteRequested;

  return (
    <PermissionGate>
      {needsFace ? <FaceRecognition /> : <Timer />}
      {remoteRequested && !isCheckedIn && (
        <TouchableOpacity style={styles.link} onPress={() => setRemoteRequested(false)}>
          <Text style={styles.linkText}>Cancel working from home</Text>
        </TouchableOpacity>
      )}
      {canOfferRemote && (
        <View style={styles.remote}>
          <TouchableOpacity style={styles.button} onPress={() => setRemoteRequested(true)}>
            <Text style={styles.buttonText}>Work from home today</Text>
          </TouchableOpacity>
        </View>
      )}
    </PermissionGate>
  );
}

const styles = StyleSheet.create({
  remote: { position: "absolute", bottom: 24, left: 0, right: 0, alignItems: "center" },
  button: { backgroundColor: "#442c91", paddingVertical: 12, paddingHorizontal: 28, borderRadius: 8 },
  buttonText: { color: "#fff", fontWeight: "bold", fontSize: 16 },
  link: { alignItems: "center", padding: 12 },
  linkText: { color: "#6A4CAF", fontSize: 16, textDecorationLine: "underline" },
});
