import Timer from "@/components/timer";

import React, { useContext } from "react";
import { AppContext } from "../AppContext";
import FaceRecognition from "@/components/face_recog";

export default function App() {
  const { isInsideGeofence, isFaceScanned } = useContext(AppContext);

  if (isInsideGeofence && !isFaceScanned) {
    return <FaceRecognition />;
  }

  return <Timer />;
}
