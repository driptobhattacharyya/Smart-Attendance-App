# Attendify Mobile App

The employee app, built with React Native and Expo. For the overall project and the backend and admin setup, see the [root README](../README.md).

## Setup

1. Copy the environment file and set the API address:

   ```bash
   cp .env.example .env
   ```

   `EXPO_PUBLIC_API_URL` must be your computer's LAN IP, for example `http://192.168.1.10:8000/api/v1`. Use `http://10.0.2.2:8000/api/v1` on the Android emulator.

   `PLAY_INTEGRITY_CLOUD_PROJECT_NUMBER` (optional) is the Google Cloud project number linked to the app in the Play Console. With it the app sends a Play Integrity token with check-in and heartbeats; without it only the root check is sent. The server decides what to do with the evidence.

   `GOOGLE_MAPS_API_KEY` is read at build time for the Android map. It is optional for development.

2. Install dependencies:

   ```bash
   npm install
   ```

## Running

Bluetooth and the camera do not work in Expo Go, so run a **development build**:

```bash
npx expo run:android
```

Once a development build is installed, `npx expo start` serves the JavaScript bundle to it.

## Accounts

There is no self-registration. Your company creates your account and you receive an invite email. Open the link (`attendify://invite?token=...`) or choose **I have an invite code** on the login screen to set your password. Sign in with email, or with company code + employee ID.

## Tests

```bash
npx jest
```

Use `npx jest` for a single run. `npm test` starts Jest in watch mode.

## Learn more

- [Expo documentation](https://docs.expo.dev/)
- [Development builds](https://docs.expo.dev/develop/development-builds/introduction/)
