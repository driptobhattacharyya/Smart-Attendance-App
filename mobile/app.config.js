// Extends app.json with values that must not be committed.
// GOOGLE_MAPS_API_KEY is read at build time (set it in EAS secrets or your shell).
module.exports = ({ config }) => ({
  ...config,
  android: {
    ...config.android,
    config: {
      ...(config.android?.config ?? {}),
      googleMaps: { apiKey: process.env.GOOGLE_MAPS_API_KEY ?? "" },
    },
  },
});
