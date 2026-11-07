import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
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
