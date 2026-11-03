import { expect, test } from "@playwright/test";

const CID = "0123456789abcdef0123456789abcdef";

test("page renders and completes a real API round trip", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Plan on a Page", level: 1 })).toBeVisible();
  await expect(page.getByTestId("status-result")).toHaveText("Service is ok (deployed).");
});

test("user can re-run the API interaction", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("status-result")).toBeVisible();
  const request = page.waitForRequest("**/api/status");
  await page.getByRole("button", { name: "Check again" }).click();
  await request;
  await expect(page.getByTestId("status-result")).toBeVisible();
});

test("API failure shows a generic message and reference, never server detail", async ({ page }) => {
  await page.route("**/api/status", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "internal_error",
          message: "Traceback (most recent call last): secret-config-value",
          correlation_id: CID,
        },
      }),
    }),
  );
  await page.goto("/");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("Something went wrong. Please try again.");
  await expect(alert).toContainText(CID);
  await expect(page.locator("body")).not.toContainText("Traceback");
  await expect(page.locator("body")).not.toContainText("secret-config-value");
});

test("a real unknown API path returns the JSON envelope from the deployed server", async ({ request }) => {
  const response = await request.get("/api/does-not-exist");
  expect(response.status()).toBe(404);
  const { error } = await response.json();
  expect(error.correlation_id).toBe(response.headers()["x-correlation-id"]);
  expect(response.headers()["content-security-policy"]).toContain("default-src 'self'");
});

test("no console errors or CSP violations on load", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (msg) => msg.type() === "error" && problems.push(msg.text()));
  page.on("pageerror", (err) => problems.push(err.message));
  await page.goto("/");
  await expect(page.getByTestId("status-result")).toBeVisible();
  expect(problems).toEqual([]);
});

test("user can complete the canvas and see all required fields satisfied", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("canvas-progress")).toContainText("14 required fields remaining.");
  for (const label of [
    "Solution Name",
    "Team",
    "Current Process",
    "Pain Points",
    "Future State Vision",
    "What do we want to achieve through AI?",
    "What does success look like?",
    "Who will use this solution?",
    "What decision will it improve?",
    "What data is needed?",
    "What systems must connect?",
    "Key risks and dependencies?",
  ]) {
    await page.getByLabel(label).fill("x");
  }
  await page.getByRole("radio", { name: "High" }).check();
  await page.getByRole("checkbox", { name: "Analyse Data" }).check();
  await expect(page.getByTestId("canvas-progress")).toHaveText("All required fields are complete.");
});
