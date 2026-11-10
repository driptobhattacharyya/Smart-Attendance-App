import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Same-origin /api in development too, so no CORS and no VITE_API_URL needed locally.
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: "charts", test: /node_modules[\\/](recharts|d3-)/ },
            { name: "maps", test: /node_modules[\\/](leaflet|react-leaflet|@react-leaflet)/ },
          ],
        },
      },
    },
  },
});
