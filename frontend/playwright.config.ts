import { defineConfig } from "@playwright/test";
import { E2E_ACCESS_CODE_HASH, E2E_COOKIE_SIGNING_KEY } from "./e2e/access";

// Smoke checks run against the production build served by FastAPI on one origin,
// exactly as in deployment, in both browsers the ROC users have: Chrome and Edge.
const PORT = Number(process.env.E2E_PORT ?? 8123);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [
    { name: "chrome", use: { channel: "chrome" } },
    { name: "edge", use: { channel: "msedge" } },
  ],
  webServer: {
    command: `uv run --frozen uvicorn --factory app.main:create_app --host 127.0.0.1 --port ${PORT}`,
    cwd: "../backend",
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: !process.env.CI,
    // `test` mode: env-provided secrets and an in-memory session store, so the real access
    // flow (cookies, CSRF, sessions) runs end to end without AWS. Never used when deployed.
    env: {
      APP_ENV: "test",
      STATIC_DIR: "../frontend/dist",
      ACCESS_CODE_HASH: E2E_ACCESS_CODE_HASH,
      COOKIE_SIGNING_KEY: E2E_COOKIE_SIGNING_KEY,
    },
  },
});
