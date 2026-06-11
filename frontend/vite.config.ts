import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

declare const process: { env: Record<string, string | undefined> };

const frontendPort = Number(process.env.WMESM_FRONTEND_PORT ?? "0");
const backendPort = Number(process.env.WMESM_BACKEND_PORT ?? "8000");

export default defineConfig({
  plugins: [react()],
  server: {
    port: frontendPort,
    strictPort: frontendPort !== 0,
    proxy: {
      "/api": {
        target: `http://localhost:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
});
