export const API_CONFIG = {
  BASE_URL: "https://5000-idx-backendattendance-1737641970767.cluster-qpa6grkipzc64wfjrbr3hsdma2.cloudworkstations.dev/api",
  ENDPOINTS: {
    LOGIN: "/user/login",
    REGISTER: "/user/register",
    GET_ATTENDANCE: "/user/get_attendance",
    FACE_RECOG: "/face_recog/recognize",
    ATTENDANCE: "/user/attendance",
    SUBMIT_REQUEST: "/user/submit_attendance_request",
    GET_OFFICES: "/office/all_offices",
    REGISTER_FACE: "/face_recog/register"
  }
} as const; 