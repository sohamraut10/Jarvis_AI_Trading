import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/ws":  { target: "ws://localhost:8765",   ws: true, changeOrigin: true },
      "/api": { target: "http://localhost:8766", changeOrigin: true },
    },
  },
});
