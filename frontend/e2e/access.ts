import { type Page, expect } from "@playwright/test";

// Test-only credentials for the Playwright backend. They protect nothing real: the backend
// runs in `test` mode with an in-memory session store and these values in its environment.
export const E2E_ACCESS_CODE = "e2e-access-code";
export const E2E_ACCESS_CODE_HASH =
  "$argon2id$v=19$m=19456,t=2,p=1$IswFjPTXxf4aMTbIKYOImA$M4PPFRYpYLN3XXXtQKeMjtb17SPunouvVpYKv5QoRUQ";
export const E2E_COOKIE_SIGNING_KEY = "e2e-cookie-signing-key-not-a-secret-0123456789";

const ACCESS_FIELD = { exact: true } as const;

// Step 1 only: the access code at "/". Lands on /start when accepted.
export async function enterCode(page: Page, code = E2E_ACCESS_CODE) {
  await page.goto("/");
  await page.getByLabel("Access code", ACCESS_FIELD).fill(code);
  await page.getByRole("button", { name: "Continue" }).click();
}

// Both steps: code, then "Start a new canvas". Lands on the conversation route.
export async function signIn(page: Page, code = E2E_ACCESS_CODE) {
  await enterCode(page, code);
  await expect(page.getByRole("heading", { name: "Start a new canvas", level: 1 })).toBeVisible();
  await page.getByRole("button", { name: "Start a new canvas" }).click();
}

export async function signInAndWait(page: Page) {
  await signIn(page);
  await expect(page.getByText(/Complete the AI Opportunity Canvas/)).toBeVisible();
}
