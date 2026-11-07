import Timer from "@/components/timer";

import React, { useContext } from "react";
import { AppContext } from "@/context/AppContext";
import FaceRecognition from "@/components/face_recog";

export default function App() {
  const { isInsideGeofence, isFaceScanned, isCheckedIn } = useContext(AppContext);

  // Ask for a face scan only when it's needed to check in.
  if (isInsideGeofence && !isFaceScanned && !isCheckedIn) {
    return <FaceRecognition />;
  }

  return <Timer />;
}
