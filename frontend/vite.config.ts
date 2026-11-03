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
    proxy: { "/api": BACKEND_DEV_URL },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
