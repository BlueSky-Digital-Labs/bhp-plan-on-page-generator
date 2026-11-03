import { expect, test } from "@playwright/test";
import { E2E_ACCESS_CODE, signIn, signInAndWait } from "./access";

const CID = "0123456789abcdef0123456789abcdef";

test("page renders and completes a real API round trip", async ({ page }) => {
  await signInAndWait(page);
  await expect(page.getByRole("heading", { name: "Plan on a Page", level: 1 })).toBeVisible();
  // Status "ok" is the API's healthy contract. The environment name depends on how the
  // backend was started (test here, deployed in AWS), so only require one.
  await expect(page.getByTestId("status-result")).toHaveText(/^Service is ok \(\w+\)\.$/);
});

test("user can re-run the API interaction", async ({ page }) => {
  await signInAndWait(page);
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
  await signIn(page);
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("Something went wrong. Please try again.");
  await expect(alert).toContainText(CID);
  await expect(page.locator("body")).not.toContainText("Traceback");
  await expect(page.locator("body")).not.toContainText("secret-config-value");
});

test("a real unknown API path returns the JSON envelope from the server", async ({ request }) => {
  const response = await request.get("/api/does-not-exist");
  expect(response.status()).toBe(404);
  const { error } = await response.json();
  expect(error.correlation_id).toBe(response.headers()["x-correlation-id"]);
  expect(response.headers()["content-security-policy"]).toContain("default-src 'self'");
});

test("no console errors or CSP violations while signing in and using the app", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (msg) => {
    // The first load asks "am I signed in?" and gets a 401: expected, and logged by the browser.
    if (msg.type() === "error" && !/status of 401/.test(msg.text())) problems.push(msg.text());
  });
  page.on("pageerror", (err) => problems.push(err.message));
  await signInAndWait(page);
  await expect(page.getByTestId("status-result")).toBeVisible();
  expect(problems).toEqual([]);
});

test("user can complete the canvas and see all required fields satisfied", async ({ page }) => {
  await signInAndWait(page);
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

test.describe("access code", () => {
  test("a visitor without a session sees only the access screen", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Enter the access code" })).toBeVisible();
    await expect(page.getByText(/Complete the AI Opportunity Canvas/)).toHaveCount(0);
    await expect(page.getByLabel("Access code", { exact: true })).toHaveAttribute("type", "password");
  });

  test("a wrong code is refused with a generic message and creates no session", async ({ page, context }) => {
    await signIn(page, "definitely-wrong");
    await expect(page.getByRole("alert")).toContainText("That access code was not recognised.");
    await expect(page.getByLabel("Access code", { exact: true })).toHaveValue("");
    await expect(page.getByText(/Complete the AI Opportunity Canvas/)).toHaveCount(0);
    const names = (await context.cookies()).map((c) => c.name);
    expect(names).not.toContain("__Host-session");
    expect(names).not.toContain("__Host-grant");
  });

  test("the right code opens a private session whose cookie JavaScript cannot read", async ({ page, context }) => {
    await signInAndWait(page);
    const session = (await context.cookies()).find((c) => c.name === "__Host-session");
    expect(session).toBeDefined();
    expect(session).toMatchObject({ httpOnly: true, secure: true, sameSite: "Lax", path: "/" });
    expect(await page.evaluate(() => document.cookie)).not.toContain("__Host-session");
    // Session cookies carry no code and no idea content.
    expect(session!.value).not.toContain(E2E_ACCESS_CODE);
  });

  test("the session survives a reload, and signing out ends it", async ({ page }) => {
    await signInAndWait(page);
    await page.reload();
    await expect(page.getByText(/Complete the AI Opportunity Canvas/)).toBeVisible();

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Enter the access code" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: "Enter the access code" })).toBeVisible();
  });

  test("a second browser is a separate visitor and must enter the code itself", async ({ browser, page }) => {
    await signInAndWait(page);
    const other = await browser.newContext();
    const otherPage = await other.newPage();
    await otherPage.goto("/");
    await expect(otherPage.getByRole("heading", { name: "Enter the access code" })).toBeVisible();
    // Signing the second one out does not touch the first.
    await signInAndWait(otherPage);
    await otherPage.getByRole("button", { name: "Sign out" }).click();
    await page.reload();
    await expect(page.getByText(/Complete the AI Opportunity Canvas/)).toBeVisible();
    await other.close();
  });
});
