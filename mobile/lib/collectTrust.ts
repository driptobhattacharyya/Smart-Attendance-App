// Gathers the trust evidence for one check-in / heartbeat from a position fix.
import { fetchIntegrityNonce } from "./attendanceApi";
import { collectDeviceEvidence } from "./deviceIntegrity";
import { buildLocationEvidence, buildTrustPayload, PositionLike, TrustPayload } from "./trust";
import { Platform } from "react-native";

export async function collectTrust(position: PositionLike): Promise<TrustPayload> {
  const device = await collectDeviceEvidence(fetchIntegrityNonce);
  return buildTrustPayload(buildLocationEvidence(position, Platform.OS), device);
}
