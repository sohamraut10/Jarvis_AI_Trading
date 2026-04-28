import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Termux config: termux_server.py runs WS on 8765, HTTP API on 8766
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/ws": {
        target: "ws://localhost:8765",
        ws: true,
        configure: (proxy) => {
          proxy.on("error", (err) => {
            // EPIPE = client disconnected before we finished writing — harmless
            if (err.code !== "EPIPE" && err.code !== "ECONNRESET") {
              console.error("[ws proxy]", err.message);
            }
          });
        },
      },
      "/api": {
        target: "http://localhost:8766",
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("error", (err) => {
            if (err.code !== "EPIPE" && err.code !== "ECONNRESET") {
              console.error("[api proxy]", err.message);
            }
          });
        },
      },
    },
  },
});
