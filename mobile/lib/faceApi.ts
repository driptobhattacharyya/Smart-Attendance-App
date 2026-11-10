// Face endpoints and liveness protocol (architecture section 8, docs/face-liveness.md).
// Paths and payload builders are all here so they are easy to adjust.
//
//   GET  /face/challenge -> {challenge_id, action: turn_left|turn_right|blink, expires_in: 60, token}
//   POST /face/verify    multipart: challenge_token + 2-6 `frames` (we send 3) -> {verified, ...}
//   POST /face/enroll    multipart: 1-3 `frames`                               -> {enrolled}
//
// `token` is the signed challenge (face-liveness.md: sign_challenge); if the server only returns
// `challenge_id` that is sent instead. Each challenge is single use: after ANY failure fetch a
// new one before retrying.
import { apiFetch } from "./api";
import { isApiError } from "./errors";

export const FACE_ENDPOINTS = {
  CHALLENGE: "/face/challenge",
  ENROLL: "/face/enroll",
  VERIFY: "/face/verify",
} as const;

export const FRAME_FIELD = "frames";
export const CHALLENGE_FIELD = "challenge_token";
/** Frames per verification (server accepts 2-6) and per enrolment (1-3). */
export const VERIFY_FRAME_COUNT = 3;
export const ENROLL_FRAME_COUNT = 3;

export type FaceAction = "turn_left" | "turn_right" | "blink";

export type FaceChallenge = {
  challenge_id: string;
  action: FaceAction;
  expires_in: number;
  /** What goes back to /face/verify. */
  token: string;
};

export type Frame = { uri: string };

export type FaceVerifyResult = {
  verified: boolean;
  reason?: string;
  liveness_score?: number;
  [key: string]: unknown;
};

export const ACTION_INSTRUCTION: Record<FaceAction, string> = {
  turn_left: "Slowly turn your head to the left",
  turn_right: "Slowly turn your head to the right",
  blink: "Look at the camera and blink",
};

export function normalizeChallenge(data: any): FaceChallenge {
  return {
    challenge_id: String(data?.challenge_id ?? ""),
    action: (data?.action as FaceAction) ?? "turn_left",
    expires_in: Number(data?.expires_in) > 0 ? Number(data.expires_in) : 60,
    token: String(data?.token ?? data?.challenge_token ?? data?.challenge ?? data?.challenge_id ?? ""),
  };
}

export async function fetchChallenge(): Promise<FaceChallenge> {
  return normalizeChallenge(await apiFetch(FACE_ENDPOINTS.CHALLENGE));
}

const frameFile = (frame: Frame, index: number) =>
  ({ uri: frame.uri, name: `frame${index}.jpg`, type: "image/jpeg" }) as any;

/** Multipart body for /face/verify: frame 0 is the frontal one, the rest follow in capture order. */
export function buildVerifyForm(challenge: Pick<FaceChallenge, "token">, frames: Frame[]): FormData {
  const form = new FormData();
  form.append(CHALLENGE_FIELD, challenge.token);
  frames.forEach((frame, i) => form.append(FRAME_FIELD, frameFile(frame, i)));
  return form;
}

export function buildEnrollForm(frames: Frame[]): FormData {
  const form = new FormData();
  frames.forEach((frame, i) => form.append(FRAME_FIELD, frameFile(frame, i)));
  return form;
}

export const verifyFace = (challenge: FaceChallenge, frames: Frame[]) =>
  apiFetch<FaceVerifyResult>(FACE_ENDPOINTS.VERIFY, { form: buildVerifyForm(challenge, frames) });

export const enrollFace = (frames: Frame[]) =>
  apiFetch<{ enrolled?: boolean }>(FACE_ENDPOINTS.ENROLL, { form: buildEnrollForm(frames) });

// ---- failures -------------------------------------------------------------------------

export type FaceFailure = {
  code: string;
  message: string;
  /** Whether the user can simply try again (always with a fresh challenge). */
  retry: boolean;
};

const MSG: Record<string, string> = {
  challenge_expired: "That took too long. Let's try again.",
  challenge_used: "That check was already used. Let's try again.",
  invalid_challenge: "That check was not valid. Let's try again.",
  bad_frame_count: "The photos could not be read. Please try again.",
  frame_too_large: "The photos were too large. Please try again.",
  no_face: "We could not see your face. Face the camera in good light and keep your whole face in the frame.",
  multiple_faces: "More than one face is in view. Make sure only you are in the frame.",
  face_too_small: "Your face is too far away. Move closer to the camera.",
  too_dark: "It is too dark. Move to a brighter place.",
  too_bright: "It is too bright. Avoid strong light directly behind or on your face.",
  too_blurry: "The picture was blurry. Hold the phone steady and try again.",
  static_frames: "We did not see you move. Follow the instruction on the screen during the scan.",
  identity_changed: "The scan was not consistent. Keep only your own face in view and try again.",
  face_mismatch: "That did not match your enrolled face. Try again, and take off glasses or a hat if you can.",
  challenge_failed: "We did not see the head movement. Turn slowly and clearly when the ring starts.",
  spoof_detected: "We could not confirm that this is a live face. Use your real face, not a photo or screen.",
  not_enrolled: "You have not enrolled your face yet.",
  consent_required: "Please accept the privacy notice first.",
  module_disabled: "Face check is not enabled for your company.",
  rate_limited: "Too many attempts. Please wait a moment and try again.",
};

/** Codes where trying again will not help. */
const NO_RETRY = new Set(["not_enrolled", "consent_required", "module_disabled"]);

/** Friendly failure for an ApiError (error.code) or a 200 result with {verified:false, reason}. */
export function describeFaceFailure(input: unknown): FaceFailure {
  let code = "unknown";
  let serverMessage: string | undefined;
  if (isApiError(input)) {
    code = typeof input.details?.reason === "string" ? input.details.reason : input.code;
    serverMessage = input.message;
    if (input.status === 0) {
      return { code: "network_error", message: input.message, retry: true };
    }
  } else if (input && typeof input === "object") {
    code = String((input as any).reason ?? (input as any).code ?? "unknown");
  }
  const message = MSG[code] ?? serverMessage ?? "Face check failed. Please try again.";
  return { code, message, retry: !NO_RETRY.has(code) };
}

export const isNotEnrolled = (failure: FaceFailure) => failure.code === "not_enrolled";
