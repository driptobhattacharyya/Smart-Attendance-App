import React, { useContext } from "react";
import { Alert } from "react-native";
import LivenessCapture from "@/components/LivenessCapture";
import { AppContext } from "@/context/AppContext";
import { FaceFailure } from "@/lib/faceApi";

/** Daily face check with liveness (turn your head). Passing it lets the app check you in. */
const FaceRecog = () => {
  const { setIsFaceScanned, setFaceEnrolled, refreshStatus } = useContext(AppContext);

  const onFailure = (failure: FaceFailure) => {
    // The face was never enrolled (or was removed): send the user to the enrolment screen.
    if (failure.code === "not_enrolled") {
      Alert.alert("Face not registered", failure.message);
      setFaceEnrolled(false);
    }
  };

  return (
    <LivenessCapture
      mode="verify"
      onFailure={onFailure}
      onSuccess={() => {
        setIsFaceScanned(true);
        refreshStatus();
      }}
    />
  );
};

export default FaceRecog;
