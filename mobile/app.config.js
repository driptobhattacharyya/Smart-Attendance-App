// Extends app.json with values that must not be committed.
// GOOGLE_MAPS_API_KEY is read at build time (set it in EAS secrets or your shell).
// PLAY_INTEGRITY_CLOUD_PROJECT_NUMBER is the Google Cloud project NUMBER linked to the app in
// the Play Console; the app uses it to request Play Integrity tokens (lib/deviceIntegrity.ts).
module.exports = ({ config }) => ({
  ...config,
  android: {
    ...config.android,
    config: {
      ...(config.android?.config ?? {}),
      googleMaps: { apiKey: process.env.GOOGLE_MAPS_API_KEY ?? "" },
    },
  },
  extra: {
    ...(config.extra ?? {}),
    playIntegrityCloudProjectNumber: process.env.PLAY_INTEGRITY_CLOUD_PROJECT_NUMBER ?? "",
  },
});
