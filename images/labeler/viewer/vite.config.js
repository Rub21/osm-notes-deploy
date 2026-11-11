import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by the labeler at / (main page). In dev, the API runs on the labeler (port 8090).
export default defineConfig({
  base: "/",
  plugins: [react()],
  build: { chunkSizeWarningLimit: 1500 },  // maplibre-gl is ~800 kB
  server: {
    proxy: { "/api": "http://localhost:8090", "/auth": "http://localhost:8090" },
  },
});
