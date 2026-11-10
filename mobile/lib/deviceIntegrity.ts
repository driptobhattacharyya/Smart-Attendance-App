// Native device checks for the trust signals. Everything is best effort and never throws:
// if a check cannot run, the evidence is simply missing/false and the server decides.
//
// - Root detection: `jail-monkey` (RootBeer based: su binaries, test-keys, Magisk, dangerous
//   props, RW paths, ...). It autolinks, so it needs no Expo config plugin.
// - Play Integrity: `@expo/app-integrity` (Expo's own module, SDK 58). It needs the Google
//   Cloud project NUMBER linked to the Play Console app, set through the environment variable
//   PLAY_INTEGRITY_CLOUD_PROJECT_NUMBER at build time (app.config.js puts it in
//   expo.extra.playIntegrityCloudProjectNumber). Without it no token is sent.
import * as AppIntegrity from "@expo/app-integrity";
import Constants from "expo-constants";
import JailMonkey from "jail-monkey";
import { Platform } from "react-native";
import { buildDeviceEvidence, DeviceEvidence } from "./trust";

const INTEGRITY_TOKEN_TTL_MS = 10 * 60 * 1000;

export const appVersion = (): string => Constants.expoConfig?.version ?? "0.0.0";

export function cloudProjectNumber(): string | null {
  const value = (Constants.expoConfig?.extra as any)?.playIntegrityCloudProjectNumber;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function isRooted(): boolean {
  try {
    return Boolean(JailMonkey.isJailBroken());
  } catch {
    return false;
  }
}

let prepared = false;
let cached: { token: string; at: number } | null = null;

/**
 * A Play Integrity token bound to `nonce` (a server-issued value, used as the request hash).
 * Tokens are slow and rate limited by Google, so one is reused for 10 minutes.
 */
export async function getIntegrityToken(nonce: string | null): Promise<string | null> {
  if (Platform.OS !== "android" || !nonce) return null;
  const project = cloudProjectNumber();
  if (!project) return null;
  if (cached && Date.now() - cached.at < INTEGRITY_TOKEN_TTL_MS) return cached.token;
  try {
    if (!prepared) {
      await AppIntegrity.prepareIntegrityTokenProviderAsync(project);
      prepared = true;
    }
    const token = await AppIntegrity.requestIntegrityCheckAsync(nonce);
    cached = { token, at: Date.now() };
    return token;
  } catch (error) {
    console.warn("Play Integrity token unavailable", error);
    return null;
  }
}

/**
 * The `device` evidence. `nonce` is fetched lazily (only when a token is actually needed),
 * so heartbeats within the cache window cost nothing extra.
 */
export async function collectDeviceEvidence(
  getNonce?: () => Promise<string | null>
): Promise<DeviceEvidence> {
  let token: string | null = null;
  try {
    if (cached && Date.now() - cached.at < INTEGRITY_TOKEN_TTL_MS) token = cached.token;
    else if (getNonce && Platform.OS === "android" && cloudProjectNumber()) {
      token = await getIntegrityToken(await getNonce());
    }
  } catch {
    token = null;
  }
  return buildDeviceEvidence({
    platform: Platform.OS,
    appVersion: appVersion(),
    rooted: isRooted(),
    integrityToken: token,
  });
}
