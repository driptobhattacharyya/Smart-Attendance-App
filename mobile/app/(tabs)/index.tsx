import Timer from "@/components/timer";

import React, { useContext } from "react";
import { AppContext } from "@/context/AppContext";
import FaceRecognition from "@/components/face_recog";
import PermissionGate from "@/components/PermissionGate";

export default function App() {
  const { isInsideGeofence, isFaceScanned, isCheckedIn } = useContext(AppContext);

  return (
    <PermissionGate>
      {/* Ask for a face scan only when it's needed to check in. */}
      {isInsideGeofence && !isFaceScanned && !isCheckedIn ? <FaceRecognition /> : <Timer />}
    </PermissionGate>
  );
}
