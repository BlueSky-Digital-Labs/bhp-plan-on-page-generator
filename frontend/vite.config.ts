import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Local development only: the browser talks to Vite, and Vite forwards /api to FastAPI.
// In deployment FastAPI serves the built assets itself, so the app is same-origin and
// no CORS configuration exists anywhere.
const BACKEND_DEV_URL = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // changeOrigin must stay false: the backend rejects state-changing requests whose Origin does
    // not match the Host it receives, so the browser's Host (localhost:5173) has to reach it intact.
    proxy: { "/api": { target: BACKEND_DEV_URL, changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
