import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import Svg, { Circle } from "react-native-svg";
import {
  ACTION_INSTRUCTION,
  describeFaceFailure,
  ENROLL_FRAME_COUNT,
  enrollFace,
  FaceChallenge,
  FaceFailure,
  fetchChallenge,
  VERIFY_FRAME_COUNT,
  verifyFace,
} from "@/lib/faceApi";
import { captureFrames } from "@/lib/liveness";

type Phase = "loading" | "ready" | "capturing" | "submitting" | "failed";

type Props = {
  /** verify: get a challenge and capture 3 frames while the user performs it. enroll: 3 plain frames. */
  mode: "verify" | "enroll";
  onSuccess: () => void;
  /** Called with every failure, e.g. to react to `not_enrolled`. */
  onFailure?: (failure: FaceFailure) => void;
};

const SIZE = 260;
const RING = 292;
const STROKE = 6;
const RADIUS = (RING - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/**
 * Camera with a progress ring. Verification follows the liveness protocol: the server gives a
 * one-time challenge ("turn your head left"), the app takes a frontal frame and then two more
 * while the user moves, compresses them and uploads them with the challenge token.
 */
export default function LivenessCapture({ mode, onSuccess, onFailure }: Props) {
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView | null>(null);
  const [cameraReady, setCameraReady] = useState(false);
  const [phase, setPhase] = useState<Phase>(mode === "verify" ? "loading" : "ready");
  const [challenge, setChallenge] = useState<FaceChallenge | null>(null);
  const [progress, setProgress] = useState(0);
  const [failure, setFailure] = useState<FaceFailure | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const fail = useCallback(
    (f: FaceFailure) => {
      if (!mounted.current) return;
      setFailure(f);
      setPhase("failed");
      onFailure?.(f);
    },
    [onFailure]
  );

  // A challenge is single use, so every attempt starts with a new one.
  const prepare = useCallback(async () => {
    setFailure(null);
    setProgress(0);
    if (mode === "enroll") {
      setPhase("ready");
      return;
    }
    setPhase("loading");
    try {
      const next = await fetchChallenge();
      if (!mounted.current) return;
      setChallenge(next);
      setPhase("ready");
    } catch (error) {
      fail(describeFaceFailure(error));
    }
  }, [mode, fail]);

  useEffect(() => {
    prepare();
  }, [prepare]);

  const start = async () => {
    const camera = cameraRef.current;
    if (!camera || phase !== "ready") return;
    setPhase("capturing");
    setProgress(0);
    try {
      const frames = await captureFrames(camera, {
        count: mode === "verify" ? VERIFY_FRAME_COUNT : ENROLL_FRAME_COUNT,
        onProgress: (p) => mounted.current && setProgress(p),
      });
      if (!mounted.current) return;
      setPhase("submitting");
      if (mode === "enroll") {
        await enrollFace(frames);
        if (mounted.current) onSuccess();
        return;
      }
      const result = await verifyFace(challenge!, frames);
      if (!mounted.current) return;
      if (result.verified) onSuccess();
      else fail(describeFaceFailure(result));
    } catch (error) {
      fail(describeFaceFailure(error));
    }
  };

  if (!permission) return <View style={styles.container} />;
  if (!permission.granted) {
    return (
      <View style={styles.container}>
        <Text style={styles.message}>We need your permission to use the camera for the face check.</Text>
        <TouchableOpacity style={styles.button} onPress={requestPermission}>
          <Text style={styles.buttonText}>Allow camera</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const instruction =
    mode === "verify" && challenge ? ACTION_INSTRUCTION[challenge.action] : "Look straight at the camera";
  const busy = phase === "loading" || phase === "submitting";

  let headline = "";
  let detail = "";
  if (phase === "loading") headline = "Getting ready...";
  else if (phase === "ready") {
    headline = mode === "verify" ? "Look straight at the camera, then tap Start" : "Look straight at the camera";
    detail =
      mode === "verify"
        ? `When the ring starts: ${instruction.toLowerCase()}.`
        : "Keep your face in the circle and hold still while we take a few photos.";
  } else if (phase === "capturing") {
    headline = mode === "verify" ? instruction : "Hold still";
  } else if (phase === "submitting") headline = "Checking...";
  else if (phase === "failed") headline = failure?.message ?? "Face check failed.";

  const canRetry = phase === "failed" && failure?.retry !== false;

  return (
    <View style={styles.container}>
      <Text style={styles.header}>{mode === "verify" ? "Face check" : "Register your face"}</Text>
      <View style={styles.stage}>
        <Svg width={RING} height={RING} style={StyleSheet.absoluteFill}>
          <Circle cx={RING / 2} cy={RING / 2} r={RADIUS} stroke="#d9c2f0" strokeWidth={STROKE} fill="none" />
          <Circle
            cx={RING / 2}
            cy={RING / 2}
            r={RADIUS}
            stroke="#442c91"
            strokeWidth={STROKE}
            fill="none"
            strokeLinecap="round"
            strokeDasharray={`${CIRCUMFERENCE} ${CIRCUMFERENCE}`}
            strokeDashoffset={CIRCUMFERENCE * (1 - progress)}
            transform={`rotate(-90 ${RING / 2} ${RING / 2})`}
          />
        </Svg>
        <View style={styles.cameraClip}>
          <CameraView
            ref={cameraRef}
            style={styles.camera}
            facing="front"
            onCameraReady={() => setCameraReady(true)}
          />
        </View>
      </View>

      <Text style={[styles.headline, phase === "failed" && styles.failure]}>{headline}</Text>
      {detail ? <Text style={styles.detail}>{detail}</Text> : null}
      {busy && <ActivityIndicator style={styles.spinner} size="small" color="#442c91" />}

      {phase === "ready" && (
        <TouchableOpacity
          style={[styles.button, !cameraReady && styles.disabled]}
          onPress={start}
          disabled={!cameraReady}
        >
          <Text style={styles.buttonText}>{cameraReady ? "Start" : "Starting camera..."}</Text>
        </TouchableOpacity>
      )}
      {canRetry && (
        <TouchableOpacity style={styles.button} onPress={prepare}>
          <Text style={styles.buttonText}>Try again</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 20, backgroundColor: "#f4eefe", alignItems: "center" },
  header: { fontSize: 22, fontWeight: "bold", color: "#442c91", marginTop: 30, marginBottom: 20 },
  stage: { width: RING, height: RING, alignItems: "center", justifyContent: "center", marginBottom: 20 },
  cameraClip: { width: SIZE, height: SIZE, borderRadius: SIZE / 2, overflow: "hidden", backgroundColor: "#000" },
  camera: { flex: 1 },
  headline: { fontSize: 20, fontWeight: "600", color: "#442c91", textAlign: "center", marginBottom: 8 },
  failure: { color: "#B3261E", fontSize: 17 },
  detail: { fontSize: 15, color: "#442c91", textAlign: "center", marginBottom: 12 },
  message: { textAlign: "center", paddingBottom: 10, marginTop: 40 },
  spinner: { marginVertical: 12 },
  button: { marginTop: 12, backgroundColor: "#442c91", paddingVertical: 12, paddingHorizontal: 32, borderRadius: 8 },
  disabled: { opacity: 0.6 },
  buttonText: { color: "white", fontWeight: "bold", fontSize: 16 },
});
