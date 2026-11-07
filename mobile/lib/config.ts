// The API address comes from EXPO_PUBLIC_API_URL (see .env.example).
// On an Android emulator, "localhost" is the emulator itself: use http://10.0.2.2:8000/api.
const BASE_URL = (process.env.EXPO_PUBLIC_API_URL ?? "http://localhost:8000/api").replace(/\/$/, "");

export const API_CONFIG = {
  BASE_URL,
  ENDPOINTS: {
    LOGIN: "/auth/login",
    REGISTER: "/auth/register",
    ME: "/auth/me",
    GET_OFFICES: "/offices",
    REGISTER_FACE: "/face/enroll",
    FACE_RECOG: "/face/verify",
    ATTENDANCE_STATUS: "/attendance/status",
    CHECK_IN: "/attendance/check-in",
    HEARTBEAT: "/attendance/heartbeat",
    CHECK_OUT: "/attendance/check-out",
    MY_BEACONS: "/beacons/mine",
    GET_ATTENDANCE: "/attendance/me",
    SUBMIT_REQUEST: "/requests",
    MY_REQUESTS: "/requests/me",
  },
} as const;
