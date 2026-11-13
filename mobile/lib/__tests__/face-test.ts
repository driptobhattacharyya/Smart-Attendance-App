jest.mock("expo-image-manipulator", () => ({ ImageManipulator: {}, SaveFormat: { JPEG: "jpeg" } }));
jest.mock("../api", () => ({ apiFetch: jest.fn() }));

// Node's FormData cannot hold React Native file objects; record the appended parts instead.
class RecordingFormData {
  parts: { fieldName: string; value: any }[] = [];
  append(fieldName: string, value: any) {
    this.parts.push({ fieldName, value });
  }
}
(global as any).FormData = RecordingFormData;

import { ApiError } from "../errors";
import {
  buildEnrollForm,
  buildVerifyForm,
  CHALLENGE_FIELD,
  describeFaceFailure,
  FRAME_FIELD,
  normalizeChallenge,
} from "../faceApi";
import { captureOffsets, fitWithin } from "../liveness";

describe("frame sizing", () => {
  it("scales the long side down to 640 and keeps the ratio", () => {
    expect(fitWithin(3000, 4000)).toEqual({ width: 480, height: 640 });
    expect(fitWithin(4000, 3000)).toEqual({ width: 640, height: 480 });
  });
  it("never scales up", () => {
    expect(fitWithin(320, 240)).toEqual({ width: 320, height: 240 });
  });
  it("spreads three frames over 1.5 s, first one immediately", () => {
    expect(captureOffsets(3)).toEqual([0, 750, 1500]);
    expect(captureOffsets(1)).toEqual([0]);
  });
});

describe("challenge", () => {
  it("prefers the signed token and falls back to the id", () => {
    expect(normalizeChallenge({ challenge_id: "c1", action: "turn_right", expires_in: 60, token: "signed" })).toEqual({
      challenge_id: "c1",
      action: "turn_right",
      expires_in: 60,
      token: "signed",
    });
    expect(normalizeChallenge({ challenge_id: "c1", action: "turn_left" }).token).toBe("c1");
  });
});

describe("multipart bodies", () => {
  const parts = (form: FormData) =>
    (form as any).parts.map((p: any) => [p.fieldName, typeof p.value === "string" ? p.value : p.value.uri]);

  it("verify: challenge token then frames in capture order", () => {
    const form = buildVerifyForm({ token: "tok" }, [{ uri: "f0.jpg" }, { uri: "f1.jpg" }, { uri: "f2.jpg" }]);
    expect(parts(form)).toEqual([
      [CHALLENGE_FIELD, "tok"],
      [FRAME_FIELD, "f0.jpg"],
      [FRAME_FIELD, "f1.jpg"],
      [FRAME_FIELD, "f2.jpg"],
    ]);
  });

  it("enroll: frames only", () => {
    expect(parts(buildEnrollForm([{ uri: "a.jpg" }]))).toEqual([[FRAME_FIELD, "a.jpg"]]);
  });
});

describe("rejection messages", () => {
  const codes = [
    "challenge_expired",
    "no_face",
    "multiple_faces",
    "face_too_small",
    "too_dark",
    "too_bright",
    "too_blurry",
    "static_frames",
    "identity_changed",
    "face_mismatch",
    "challenge_failed",
    "spoof_detected",
    "not_enrolled",
  ];

  it.each(codes)("has a helpful message for %s", (code) => {
    const failure = describeFaceFailure(new ApiError("server text", 422, code));
    expect(failure.code).toBe(code);
    expect(failure.message).not.toBe("server text");
    expect(failure.message.length).toBeGreaterThan(10);
  });

  it("reads reason from a 200 {verified:false} result", () => {
    expect(describeFaceFailure({ verified: false, reason: "too_dark" })).toMatchObject({ code: "too_dark", retry: true });
  });

  it("reads details.reason from a generic error", () => {
    const err = new ApiError("Face check failed", 422, "face_check_failed", { reason: "spoof_detected" });
    expect(describeFaceFailure(err).code).toBe("spoof_detected");
  });

  it("does not offer a retry when it cannot help", () => {
    expect(describeFaceFailure(new ApiError("x", 409, "not_enrolled")).retry).toBe(false);
    expect(describeFaceFailure(new ApiError("x", 403, "module_disabled")).retry).toBe(false);
  });

  it("falls back to the server message for unknown codes, and for network errors", () => {
    expect(describeFaceFailure(new ApiError("Something odd", 500, "weird")).message).toBe("Something odd");
    const net = describeFaceFailure(new ApiError("Cannot reach the server.", 0, "network_error"));
    expect(net).toMatchObject({ code: "network_error", retry: true });
  });
});
