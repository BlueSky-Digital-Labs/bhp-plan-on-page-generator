import { readFileSync } from "node:fs";
import { type Page, expect, test } from "@playwright/test";
import { E2E_ACCESS_CODE, enterCode, signInAndWait } from "./access";

// Opens a download menu and picks a format, as a user would.
async function chooseDownload(page: Page, menu: string, item: string) {
  await page.getByRole("button", { name: new RegExp(`^${menu}`) }).click();
  await page.getByRole("menuitem", { name: item }).click();
}

// What the server says about the two PDFs. The page only shows it; the real server decides deleting.
async function mockDownloads(page: Page, canvas: boolean, appendix: boolean, revision = 3) {
  const lock = canvas ? "appendix_not_downloaded" : appendix ? "canvas_not_downloaded" : "both_not_downloaded";
  await page.route("**/api/downloads", (route) =>
    route.fulfill({
      json: {
        revision,
        canvas_downloaded: canvas,
        appendix_downloaded: appendix,
        purge_eligible: canvas && appendix,
        lock_reason: canvas && appendix ? null : lock,
        complete: true,
      },
    }),
  );
}

const CID = "0123456789abcdef0123456789abcdef";

test("page renders and completes a real API round trip", async ({ page }) => {
  await signInAndWait(page);
  await page.goto("/canvas"); // the original template form and status panel
  await expect(page.getByRole("heading", { name: "SCP AI Ideation assistant", level: 1 })).toBeVisible();
  // Status "ok" is the API's healthy contract. The environment name depends on how the
  // backend was started (test here, deployed in AWS), so only require one.
  await expect(page.getByTestId("status-result")).toHaveText(/^Service is ok \(\w+\)\.$/);
});

test("user can re-run the API interaction", async ({ page }) => {
  await signInAndWait(page);
  await page.goto("/canvas");
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
  await signInAndWait(page);
  await page.goto("/canvas");
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
  page.on("console", (msg) => msg.type() === "error" && problems.push(msg.text()));
  page.on("pageerror", (err) => problems.push(err.message));
  await signInAndWait(page);
  await page.goto("/canvas");
  await expect(page.getByTestId("status-result")).toBeVisible();
  expect(problems).toEqual([]);
});

test("user can complete the canvas and see all required fields satisfied", async ({ page }) => {
  await signInAndWait(page);
  await page.goto("/canvas");
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
  await page.getByRole("radio", { name: "Must-have" }).check();
  await page.getByRole("checkbox", { name: "Analyse Data" }).check();
  await expect(page.getByTestId("canvas-progress")).toHaveText("All required fields are complete.");
});

test.describe("access code", () => {
  test("a visitor without a session sees only the access screen", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Enter the access code" })).toBeVisible();
    await expect(page.getByLabel("Describe your idea", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("Access code", { exact: true })).toHaveAttribute("type", "password");
  });

  test("the entry page centres the access card, under the BHP logo", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/");
    await expect(page.getByLabel("Access code", { exact: true })).toBeVisible();
    await expect(page.locator("header").getByRole("img", { name: "BHP" })).toBeVisible();

    const block = (await page.locator(".entry").boundingBox())!;
    const main = (await page.locator("main").boundingBox())!;
    const viewport = page.viewportSize()!;
    expect(Math.abs(block.x + block.width / 2 - viewport.width / 2)).toBeLessThan(2); // horizontal
    expect(Math.abs(block.y + block.height / 2 - (main.y + main.height / 2))).toBeLessThan(2); // vertical
  });

  test("a wrong code is refused with a generic message and creates no session", async ({ page, context }) => {
    await enterCode(page, "definitely-wrong");
    await expect(page.getByRole("alert")).toContainText("That access code was not recognised.");
    await expect(page.getByLabel("Access code", { exact: true })).toHaveValue("");
    await expect(page.getByLabel("Describe your idea", { exact: true })).toHaveCount(0);
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
    await expect(page.getByLabel("Describe your idea", { exact: true })).toBeVisible();

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
    await expect(page.getByLabel("Describe your idea", { exact: true })).toBeVisible();
    await other.close();
  });
});

test.describe("entry and start screens", () => {
  test("opening /start directly without access returns to entry with neutral guidance", async ({ page }) => {
    await page.goto("/start");
    await expect(page.getByRole("heading", { name: "Enter the access code" })).toBeVisible();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("status")).toContainText("Your access has expired or has not been entered");
    await expect(page.getByRole("button", { name: "Start a new canvas" })).toHaveCount(0);
  });

  test("opening /conversation directly without a session returns to entry", async ({ page }) => {
    await page.goto("/conversation");
    await expect(page.getByRole("heading", { name: "Enter the access code" })).toBeVisible();
    await expect(page.getByLabel("Describe your idea", { exact: true })).toHaveCount(0);
  });

  test("a correct code leads to the start screen, which survives a refresh", async ({ page }) => {
    await enterCode(page);
    await expect(page.getByRole("heading", { name: "Start a new canvas", level: 1 })).toBeVisible();
    await expect(page).toHaveURL(/\/start$/);
    await page.reload(); // the backend serves the app and re-verifies access
    await expect(page.getByRole("heading", { name: "Start a new canvas", level: 1 })).toBeVisible();
  });

  test("the start screen explains the journey, saving and the data lifecycle", async ({ page }) => {
    await enterCode(page);
    const main = page.locator("main");
    await expect(main).toContainText("Describe your idea");
    await expect(main).toContainText("Save both the one-page canvas and the appendix");
    await expect(main).toContainText("not submit anything to a backlog");
    await expect(main).toContainText("Australian-hosted");
    await expect(main).toContainText("24 hours");
    await expect(main).toContainText("even if you close the browser");
    await expect(main).toContainText("not a statement that data-handling approval has been granted");
  });

  test("the whole flow works from the keyboard", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Access code", { exact: true }).focus();
    await page.keyboard.type(E2E_ACCESS_CODE);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Start a new canvas", level: 1 })).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Start a new canvas" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("Describe your idea", { exact: true })).toBeVisible();
  });

  test("starting again creates a fresh canvas and never resumes the earlier one", async ({ page, context }) => {
    await signInAndWait(page);
    const first = (await context.cookies()).find((c) => c.name === "__Host-session")!.value;
    await page.goto("/start"); // an existing session may start another canvas
    await page.getByRole("button", { name: "Start a new canvas" }).click();
    await expect(page.getByLabel("Describe your idea", { exact: true })).toBeVisible();
    const second = (await context.cookies()).find((c) => c.name === "__Host-session")!.value;
    expect(second).not.toBe(first);
  });

  test("a failed start is retryable and the code is never kept in browser storage", async ({ page }) => {
    await enterCode(page);
    await page.route("**/api/sessions", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "service_unavailable", message: "SERVER-TEXT", correlation_id: CID } }),
      }),
    );
    await page.getByRole("button", { name: "Start a new canvas" }).click();
    await expect(page.getByRole("alert")).toContainText("Something went wrong. Please try again.");
    await expect(page.locator("body")).not.toContainText("SERVER-TEXT");
    await expect(page.getByRole("button", { name: "Start a new canvas" })).toBeEnabled();

    await page.unroute("**/api/sessions");
    await page.getByRole("button", { name: "Start a new canvas" }).click();
    await expect(page.getByLabel("Describe your idea", { exact: true })).toBeVisible();

    const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
    expect(stored).toBe("{}{}");
  });
});

test.describe("conversation", () => {
  const IDEA = "We copy shift notes between three spreadsheets and it takes hours.";
  const idea = (page: import("@playwright/test").Page) => page.getByLabel("Describe your idea", { exact: true });
  const said = (page: import("@playwright/test").Page, text: string) =>
    page.locator(".transcript").getByText(text, { exact: true });
  const send = async (page: import("@playwright/test").Page, text: string) => {
    await page.getByLabel(/Describe your idea|Your answer/).fill(text);
    await page.getByRole("button", { name: "Send" }).click();
  };

  test("invites a rough idea, then shows the answer and a streamed reply that becomes stored history", async ({ page }) => {
    await signInAndWait(page);
    await expect(page.getByText(/in your own words/)).toBeVisible();
    await send(page, IDEA);

    // The scripted reply streams in; once confirmed it is a normal stored turn.
    await expect(page.getByText("Assistant said")).toBeVisible();
    await expect(page.getByText(/Could you tell me who would use this/)).toBeVisible();
    await expect(page.getByText(/reply in progress/)).toHaveCount(0);
    await expect(said(page, IDEA)).toHaveCount(1);
    await expect(page.getByLabel("Your answer", { exact: true })).toBeVisible();
  });

  test("a refresh shows the stored conversation without duplicating the answer", async ({ page }) => {
    await signInAndWait(page);
    await send(page, IDEA);
    await expect(page.getByText("Assistant said")).toBeVisible();
    await page.reload();
    await expect(page.getByText(/Could you tell me who would use this/)).toBeVisible();
    await expect(said(page, IDEA)).toHaveCount(1);
  });

  test("a dropped stream recovers from stored state without re-sending the answer", async ({ page }) => {
    await signInAndWait(page);
    await page.route("**/stream", (route) => route.abort());
    await send(page, IDEA);
    await expect(page.getByTestId("reply-status")).toContainText("interrupted. Your answer is saved");
    await expect(said(page, IDEA)).toHaveCount(1);

    await page.unroute("**/stream");
    await page.getByRole("button", { name: "Get the reply" }).click();
    await expect(page.getByText(/Could you tell me who would use this/)).toBeVisible();
    await expect(said(page, IDEA)).toHaveCount(1);
  });

  test("user and model markup is shown as plain text", async ({ page }) => {
    await signInAndWait(page);
    const hostile = '<img src=x onerror="document.title=\'pwned\'"> <script>document.title="pwned"</script>';
    await send(page, hostile);
    await expect(said(page, hostile)).toBeVisible();
    await expect(page.locator("main img, main script")).toHaveCount(0);
    expect(await page.title()).not.toBe("pwned");
  });

  test("a file can be attached, sent with the answer and found again after a refresh, and bad files are refused", async ({ page }) => {
    await signInAndWait(page);
    const input = page.getByTestId("file-input");
    const json = (data: object) => Buffer.from(JSON.stringify(data, null, 2));

    // A real upload to the real server: the text comes back, and the chip appears.
    await input.setInputFiles({ name: "idea.json", mimeType: "application/json", buffer: json({ idea: "Shift notes assistant", team: "Operations" }) });
    await expect(page.locator(".attachment-chip")).toContainText("idea.json");
    await page.getByLabel("Describe your idea", { exact: true }).fill("Here is our idea.");
    await page.getByRole("button", { name: "Send" }).click();

    // The answer shows what was typed and the file folded away; a refresh shows the same, stored once.
    await expect(page.getByText("Here is our idea.")).toBeVisible();
    const folded = page.locator("details.turn__file");
    await expect(folded).toHaveCount(1);
    await expect(folded.locator("summary")).toContainText("Attached file: idea.json");
    await expect(page.locator(".attachment-chip")).toHaveCount(0); // cleared once sent
    await expect(page.getByText("Assistant said")).toBeVisible();
    await page.reload();
    await expect(page.locator("details.turn__file")).toHaveCount(1);
    await page.locator("details.turn__file summary").click();
    await expect(page.locator(".turn__file-text")).toContainText('"team": "Operations"');

    // Files the server cannot use are refused in plain words and nothing is attached.
    await input.setInputFiles({ name: "broken.docx", mimeType: "application/octet-stream", buffer: Buffer.from("not a word file") });
    await expect(page.getByRole("alert")).toContainText("could not be read");
    await expect(page.locator(".attachment-chip")).toHaveCount(0);
    await input.setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
    await expect(page.getByRole("alert")).toContainText("Only PDF, Word (.docx), PowerPoint (.pptx) and JSON");
  });

  test("looks like a chat window: sides, fixed height, and the newest message stays in view", async ({ page }) => {
    test.setTimeout(60_000);
    await page.setViewportSize({ width: 1280, height: 640 });
    await signInAndWait(page);
    const storedReplies = page.locator(".turn--assistant:not(.turn--pending)");
    for (let i = 1; i <= 4; i++) {
      await send(page, `Answer number ${i} about the idea.`);
      await expect(storedReplies).toHaveCount(i);
    }

    const box = async (selector: string, which: "first" | "last") => {
      const b = await page.locator(selector)[which]().boundingBox();
      return b!;
    };
    const chat = await box(".chat", "first");
    const user = await box(".turn--user .turn__bubble", "first");
    const reply = await box(".turn--assistant .turn__bubble", "first");
    // Yours on the right, the assistant's on the left, neither spanning the whole width.
    expect(user.x + user.width / 2).toBeGreaterThan(reply.x + reply.width / 2);
    expect(reply.x).toBeLessThan(chat.x + chat.width / 2);
    expect(user.width).toBeLessThan(chat.width * 0.9);

    // The window keeps a fixed height and scrolls inside, instead of the page growing with it.
    const area = await page.locator(".chat__messages").evaluate((el) => ({
      scrolls: el.scrollHeight > el.clientHeight, atBottom: el.scrollHeight - el.scrollTop - el.clientHeight < 2,
    }));
    expect(chat.height).toBeLessThan(640);
    expect(area).toEqual({ scrolls: true, atBottom: true });

    // The newest reply is fully visible above the answer box.
    const messages = await box(".chat__messages", "first");
    const newest = await box(".turn--assistant:not(.turn--pending) .turn__bubble", "last");
    expect(newest.y + newest.height).toBeLessThanOrEqual(messages.y + messages.height + 1);
  });

  test("a new canvas starts empty and never shows the previous conversation", async ({ page }) => {
    await signInAndWait(page);
    await send(page, IDEA);
    await expect(page.getByText("Assistant said")).toBeVisible();
    await page.goto("/start");
    await page.getByRole("button", { name: "Start a new canvas" }).click();
    await expect(idea(page)).toBeVisible();
    await expect(page.getByText(IDEA)).toHaveCount(0);
    await expect(page.getByText(/in your own words/)).toBeVisible();
  });

  test("browser back returns to the conversation with its stored history", async ({ page }) => {
    await signInAndWait(page);
    await send(page, IDEA);
    await expect(page.getByText("Assistant said")).toBeVisible();
    await page.goto("/start");
    await page.goBack();
    await expect(said(page, IDEA)).toHaveCount(1);
    await expect(page.getByText(/Could you tell me who would use this/)).toBeVisible();
  });

  test("conversation responses are never cached, and no idea content reaches browser storage", async ({ page, context }) => {
    await signInAndWait(page);
    await send(page, IDEA);
    await expect(page.getByText("Assistant said")).toBeVisible();

    const history = await context.request.get("/api/conversation");
    expect(history.headers()["cache-control"]).toBe("no-store");
    expect(history.headers()["content-type"]).toContain("application/json");
    const turns = (await history.json()).turns as { turn_id: string; role: string }[];
    const streamUrl = `/api/conversation/turns/${turns.find((t) => t.role === "assistant")!.turn_id}/stream`;
    const stream = await context.request.get(streamUrl);
    expect(stream.headers()["cache-control"]).toBe("no-store");
    expect(stream.headers()["content-type"]).toContain("application/x-ndjson");

    const stored = await page.evaluate(async () => ({
      local: JSON.stringify({ ...localStorage }),
      session: JSON.stringify({ ...sessionStorage }),
      databases: (await indexedDB.databases()).length,
      caches: (await caches.keys()).length,
      workers: (await navigator.serviceWorker?.getRegistrations())?.length ?? 0,
    }));
    expect(stored).toEqual({ local: "{}", session: "{}", databases: 0, caches: 0, workers: 0 });
  });

  test("another browser cannot stream or read this conversation", async ({ page, browser }) => {
    await signInAndWait(page);
    await send(page, IDEA);
    await expect(page.getByText("Assistant said")).toBeVisible();
    const turns = (await (await page.context().request.get("/api/conversation")).json()).turns as {
      turn_id: string;
      role: string;
    }[];
    const botId = turns.find((t) => t.role === "assistant")!.turn_id;

    const other = await browser.newContext();
    const otherPage = await other.newPage();
    await signInAndWait(otherPage);
    const stolen = await other.request.get(`/api/conversation/turns/${botId}/stream`);
    expect(stolen.status()).toBe(404);
    expect(await stolen.text()).not.toContain("Could you tell me");
    expect((await (await other.request.get("/api/conversation")).json()).turns).toEqual([]);
    await other.close();
  });
});

test.describe("one-page canvas", () => {
  const FIELDS = {
    solutionName: "Shift Notes Assistant", team: "Operations", priority: "Must-have",
    opportunityTypes: ["Find Information", "Generate Reports"],
    currentProcess: "Copy notes between spreadsheets", painPoints: "Takes hours", businessImpact: "",
    futureState: "One shared summary", achieve: "Faster handover", success: "Handover takes minutes",
    kpis: "Hours saved per week", users: "Shift supervisors", decision: "What to escalate",
    data: "Shift logs", systems: "Rostering system", risks: "Sensitive details",
  };
  const READY = { revision: 3, turns: [], canvas: { fields: FIELDS, missing_required: [], ready_for_review: true } };

  test("a complete canvas offers 'Generate canvas', which renders the one-pager and is read back for confirmation", async ({ page }) => {
    await signInAndWait(page);
    // Nothing offered while the canvas is incomplete (the real server says 14 answers to go).
    await expect(page.getByRole("button", { name: "Generate canvas" })).toHaveCount(0);

    // The conversation feature is not exercised here, so the server's "ready" verdict is simulated, with
    // the answers a finished canvas always has (the canvas box appears once the conversation has begun).
    const turns = [
      { turn_id: "1".repeat(32), role: "user", text: "We copy shift notes between spreadsheets.", status: "complete" },
      { turn_id: "2".repeat(32), role: "assistant", text: "Thanks, that is everything I need.", status: "complete" },
    ];
    await page.route("**/api/conversation", (route) => route.fulfill({ json: { ...READY, turns } }));
    await page.reload();
    await page.getByRole("button", { name: "Generate canvas" }).click();

    await expect(page).toHaveURL(/\/review$/);
    await expect(page.getByRole("heading", { level: 1, name: /AI OPPORTUNITY CANVAS/ })).toBeVisible();
    const sheet = page.locator(".one-pager");
    for (const text of ["Shift Notes Assistant", "Operations", "Copy notes between spreadsheets", "Handover takes minutes", "Rostering system", "Hours saved per week"]) {
      await expect(sheet).toContainText(text);
    }
    await expect(sheet.getByRole("listitem").filter({ hasText: "(selected)" })).toHaveCount(3); // Must-have + 2 types
    await expect(sheet).toContainText("Not provided"); // the skipped optional field is left blank

    // Colours come straight from the template image.
    const colours = await page.evaluate(() => {
      const bg = (sel: string) => getComputedStyle(document.querySelector(sel)!).borderTopColor;
      return { orange: bg(".op-panel--orange"), purple: bg(".op-panel--purple"), blue: bg(".op-panel--blue"), green: bg(".op-panel--green"),
        page: getComputedStyle(document.querySelector(".one-pager")!).backgroundColor };
    });
    expect(colours).toEqual({
      orange: "rgb(243, 112, 33)", purple: "rgb(123, 97, 255)", blue: "rgb(2, 181, 226)", green: "rgb(0, 196, 140)",
      page: "rgb(30, 30, 30)",
    });

    // Back to the conversation works from the read-back.
    await page.getByRole("button", { name: "Back to the conversation" }).click();
    await expect(page).toHaveURL(/\/conversation$/);
  });

  test("the read-back must be confirmed before any download is offered, then each file downloads under the server's name", async ({ page }) => {
    await signInAndWait(page);
    await mockDownloads(page, false, false);
    let confirmed: number | null = null;
    await page.route("**/api/conversation", (route) => route.fulfill({ json: { ...READY, confirmed_revision: confirmed } }));
    await page.route("**/api/canvas/confirm", (route) => {
      expect(route.request().postDataJSON()).toEqual({ revision: READY.revision }); // the revision that was shown
      confirmed = READY.revision;
      return route.fulfill({ json: { ...READY, confirmed_revision: confirmed } });
    });
    await page.goto("/review");
    await expect(page.getByRole("heading", { level: 2, name: "Check your canvas" })).toBeVisible();
    await expect(page.getByText("the priority, Must-have, which is the one you chose")).toBeVisible();
    await expect(page.getByRole("button", { name: /^Download/ })).toHaveCount(0); // nothing before confirming

    await page.getByRole("button", { name: "Yes, this is correct" }).click();
    await expect(page.getByRole("status")).toContainText("You confirmed this version");
    await expect(page.getByRole("button", { name: /Back to the conversation/ })).toHaveCount(0); // final once confirmed

    const files = [
      ["Download canvas", "PDF", "canvas-file?format=pdf", "shift-notes-assistant-canvas-r3.pdf"],
      ["Download canvas", "PowerPoint (.pptx)", "canvas-file?format=pptx", "shift-notes-assistant-canvas-r3.pptx"],
      ["Download appendix", "PDF", "appendix?format=pdf", "shift-notes-assistant-appendix-r3.pdf"],
      ["Download appendix", "Word (.docx)", "appendix?format=docx", "shift-notes-assistant-appendix-r3.docx"],
      ["Download appendix", "JSON", "appendix?format=json", "shift-notes-assistant-appendix-r3.json"],
    ];
    for (const [menu, item, path, name] of files) {
      await page.route(`**/api/${path}&revision=3`, (route) =>
        route.fulfill({ status: 200, contentType: "application/octet-stream", headers: { "Content-Disposition": `attachment; filename="${name}"` }, body: "file-bytes" }),
      );
      const [download] = await Promise.all([page.waitForEvent("download"), chooseDownload(page, menu, item)]);
      expect(download.suggestedFilename()).toBe(name);
      expect(readFileSync(await download.path(), "utf8")).toBe("file-bytes");
    }
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("the real server refuses to confirm or make documents for a canvas that is not complete", async ({ page }) => {
    await signInAndWait(page);
    await mockDownloads(page, false, false);
    // The page is told the canvas is ready and confirmed; the server, which knows better, says no.
    await page.route("**/api/conversation", (route) => route.fulfill({ json: { ...READY, confirmed_revision: READY.revision } }));
    await page.goto("/review");
    await chooseDownload(page, "Download canvas", "PDF");
    await expect(page.getByRole("alert")).toContainText("The canvas is not complete yet");
    await chooseDownload(page, "Download appendix", "JSON");
    await expect(page.getByRole("alert")).toContainText("The canvas is not complete yet");

    await page.route("**/api/conversation", (route) => route.fulfill({ json: { ...READY, confirmed_revision: null } }));
    await page.reload();
    await page.getByRole("button", { name: "Yes, this is correct" }).click();
    await expect(page.getByRole("alert")).toContainText("The canvas is not complete yet"); // 422 from the real server
  });

  test("email: one click submits the confirmed revision, with no address or text to fill in", async ({ page }) => {
    await signInAndWait(page);
    await mockDownloads(page, true, true);
    await page.route("**/api/conversation", (route) => route.fulfill({ json: { ...READY, confirmed_revision: READY.revision } }));
    let body: unknown = null;
    await page.route("**/api/email", (route) => {
      body = route.request().postDataJSON();
      return route.fulfill({ json: { sent: 1 } });
    });
    await page.goto("/review");
    await expect(page.getByLabel("To")).toHaveCount(0); // nothing to type
    expect(body).toBeNull(); // and nothing sent until the click

    await page.getByRole("button", { name: "Submit by email" }).click();
    await expect(page.locator(".mail").getByRole("status")).toContainText("Submitted by email");
    expect(body).toEqual({ revision: READY.revision }); // only the revision goes to the server
  });

  test("email: the real server says plainly that submitting is not switched on, before it builds anything", async ({ page }) => {
    await signInAndWait(page);
    await mockDownloads(page, true, true);
    await page.route("**/api/conversation", (route) => route.fulfill({ json: { ...READY, confirmed_revision: READY.revision } }));
    await page.goto("/review");
    await page.getByRole("button", { name: "Submit by email" }).click();
    await expect(page.locator(".mail").getByRole("alert")).toContainText("not switched on yet"); // 503 email_unavailable
  });

  test("a question with a fixed list is answered by picking and sending, or by typing instead", async ({ page }) => {
    await signInAndWait(page);
    const ASKING = {
      revision: 2,
      turns: [
        { turn_id: "1".repeat(32), role: "user", text: "We copy shift notes between spreadsheets.", status: "complete" },
        { turn_id: "2".repeat(32), role: "assistant", text: "What priority would you give this idea?", status: "complete" },
      ],
      canvas: { fields: {}, missing_required: ["priority"], ready_for_review: false },
      progression: {
        phase: "interview",
        outstanding_question: {
          target: "priority",
          kind: "missing",
          text: "What priority would you give this idea?",
          choices: { multiple: false, options: ["Must-have", "Nice-to-have", "Exploratory"] },
        },
      },
    };
    await page.route("**/api/conversation", (route) => route.fulfill({ json: ASKING }));
    await page.route("**/api/downloads", (route) =>
      route.fulfill({ json: { revision: 2, canvas_downloaded: false, appendix_downloaded: false, purge_eligible: false, lock_reason: "both_not_downloaded", complete: false } }),
    );
    let sent: unknown = null;
    await page.route("**/api/conversation/turns", (route) => {
      sent = route.request().postDataJSON();
      return route.fulfill({
        status: 201,
        json: {
          revision: 3,
          user_turn: { turn_id: "3".repeat(32), role: "user", text: "Must-have", status: "complete" },
          assistant_turn: { turn_id: "4".repeat(32), role: "assistant", text: "Thanks.", status: "complete" },
        },
      });
    });
    await page.goto("/conversation");

    await expect(page.getByRole("group", { name: "Choose one" })).toBeVisible();
    await expect(page.getByLabel("Your answer")).toHaveCount(0); // no text box while a list is offered
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
    await page.getByLabel("Must-have").check();
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => sent).toMatchObject({ expected_revision: 2, text: "Must-have" });

    await page.reload();
    await page.getByRole("button", { name: "Type my own answer instead" }).click();
    await expect(page.getByLabel("Your answer")).toBeVisible();
    await expect(page.getByRole("group", { name: "Choose one" })).toHaveCount(0);
  });

  test("saving halfway: download both files as they are, stop the session, and be told how to carry on", async ({ page }) => {
    await signInAndWait(page);
    const HALF = {
      revision: 2,
      turns: [
        { turn_id: "1".repeat(32), role: "user", text: "We copy shift notes between spreadsheets.", status: "complete" },
        { turn_id: "2".repeat(32), role: "assistant", text: "Which team?", status: "complete" },
      ],
      canvas: { fields: { solutionName: "Shift notes" }, missing_required: ["team", "priority"], ready_for_review: false },
    };
    await page.route("**/api/conversation", (route) => route.fulfill({ json: HALF }));
    await page.route("**/api/access-state", (route) => route.fulfill({ json: { start_permitted: false } }));
    // The status follows what the server has sent; this stands in for its records.
    let canvasSaved = false;
    let appendixSaved = false;
    await page.route("**/api/downloads", (route) =>
      route.fulfill({
        json: {
          revision: HALF.revision,
          canvas_downloaded: canvasSaved,
          appendix_downloaded: appendixSaved,
          purge_eligible: canvasSaved && appendixSaved,
          lock_reason: canvasSaved && appendixSaved ? null : "both_not_downloaded",
          complete: false,
        },
      }),
    );
    await page.route("**/api/canvas-file**", (route) => {
      canvasSaved = true;
      return route.fulfill({ body: "%PDF-", headers: { "Content-Disposition": 'attachment; filename="shift-notes-canvas-draft-r2.pdf"' } });
    });
    await page.route("**/api/appendix**", (route) => {
      appendixSaved = true;
      return route.fulfill({ body: "%PDF-", headers: { "Content-Disposition": 'attachment; filename="shift-notes-appendix-draft-r2.pdf"' } });
    });
    let purged: unknown = null;
    await page.route("**/api/session/purge", (route) => {
      purged = route.request().postDataJSON();
      return route.fulfill({ status: 204 });
    });
    await page.goto("/conversation");

    const card = page.locator(".dl");
    await expect(card.getByRole("heading", { name: "Save your progress" })).toBeVisible();
    await expect(card.getByRole("button", { name: /^Stop session/ })).toHaveCount(0); // locked until both are saved

    await card.getByRole("button", { name: /Download canvas so far/ }).click();
    await card.getByRole("menuitem", { name: "PDF" }).click();
    await card.getByRole("button", { name: /Download appendix so far/ }).click();
    await card.getByRole("menuitem", { name: "PDF" }).click();
    await card.getByRole("button", { name: "Refresh status" }).click();

    await card.getByRole("button", { name: /^Stop session/ }).click();
    await expect(card.getByText(/attach one of the files you downloaded/)).toBeVisible();
    await card.getByRole("button", { name: "Yes, stop and delete everything" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("status")).toContainText("Your session was stopped");
    expect(purged).toEqual({ revision: HALF.revision });
  });

  test("download status: each PDF is shown on its own, deleting stays locked until the server says both are done", async ({ page }) => {
    await signInAndWait(page);
    await page.route("**/api/conversation", (route) => route.fulfill({ json: { ...READY, confirmed_revision: READY.revision } }));
    const card = page.locator(".dl");
    const cases: Array<[boolean, boolean, string, string, string]> = [
      [false, false, "Not downloaded yet", "Not downloaded yet", "the one-page canvas and the appendix"],
      [true, false, "Downloaded", "Not downloaded yet", "the appendix"],
      [false, true, "Not downloaded yet", "Downloaded", "the one-page canvas"],
    ];
    for (const [canvas, appendix, canvasText, appendixText, outstanding] of cases) {
      await page.unroute("**/api/downloads");
      await mockDownloads(page, canvas, appendix);
      await page.goto("/review");
      await expect(card.getByRole("heading", { name: "Your documents" })).toBeVisible();
      await expect(card.locator("li", { hasText: "One-page canvas" })).toContainText(canvasText);
      await expect(card.locator("li", { hasText: "Appendix" })).toContainText(appendixText);
      await expect(card).toContainText(`Still to download: ${outstanding}.`);
      await expect(page.getByRole("button", { name: /^Delete my session/ })).toHaveCount(0);
    }
    await expect(card).toContainText("It cannot check that the file was saved on your device");

    await page.unroute("**/api/downloads");
    await mockDownloads(page, true, true);
    await page.goto("/review");
    await expect(page.getByRole("button", { name: /^Delete my session/ })).toBeVisible();
    await expect(card).not.toContainText("Deleting is locked");
  });

  test("download status: a failed status check keeps deleting locked and can be retried", async ({ page }) => {
    await signInAndWait(page);
    await page.route("**/api/conversation", (route) => route.fulfill({ json: { ...READY, confirmed_revision: READY.revision } }));
    await page.route("**/api/downloads", (route) => route.fulfill({ status: 503, json: { error: { code: "service_unavailable", message: "x", correlation_id: "0".repeat(32) } } }));
    await page.goto("/review");
    await expect(page.locator(".dl").getByRole("alert")).toContainText("We could not check the download status");
    await expect(page.getByRole("button", { name: /^Delete my session/ })).toHaveCount(0);
    await page.unroute("**/api/downloads");
    await mockDownloads(page, true, true);
    await page.getByRole("button", { name: "Refresh status" }).click();
    await expect(page.getByRole("button", { name: /^Delete my session/ })).toBeVisible();
  });

  test("download status: the real server refuses to delete when its own gate is not satisfied, whatever the page shows", async ({ page }) => {
    await signInAndWait(page);
    await page.route("**/api/conversation", (route) => route.fulfill({ json: { ...READY, confirmed_revision: READY.revision } }));
    await mockDownloads(page, true, true); // the page is told both are done; the real server knows better
    await page.goto("/review");
    await page.getByRole("button", { name: /^Delete my session/ }).click();
    await page.getByRole("button", { name: "Yes, delete everything" }).click();
    // A 409 from the real server (it has no such canvas): nothing is deleted, with fixed wording.
    await expect(page.locator(".dl").getByRole("alert")).toContainText(/Deleting is still locked|The canvas changed since it was shown/);
    await expect(page).toHaveURL(/\/review$/);
    await page.request.get("/api/session").then((r) => expect(r.status()).toBe(200)); // still signed in: nothing was touched
  });

  test("deleting: after both documents, the user confirms and returns to entry with a plain notice", async ({ page }) => {
    await signInAndWait(page);
    await page.route("**/api/conversation", (route) => route.fulfill({ json: { ...READY, confirmed_revision: READY.revision } }));
    await mockDownloads(page, true, true);
    // After a real purge the session is gone; this stands in for that on the entry page.
    await page.route("**/api/access-state", (route) => route.fulfill({ json: { start_permitted: false } }));
    await page.route("**/api/session/purge", (route) => {
      expect(route.request().postDataJSON()).toEqual({ revision: READY.revision });
      return route.fulfill({ status: 204 });
    });
    await page.goto("/review");
    await page.getByRole("button", { name: /^Delete my session/ }).click();
    await expect(page.getByText(/cannot be recovered/)).toBeVisible();
    await page.getByRole("button", { name: "Keep my session" }).click();
    await expect(page.getByText(/cannot be recovered/)).toHaveCount(0);
    await page.getByRole("button", { name: /^Delete my session/ }).click();
    await page.getByRole("button", { name: "Yes, delete everything" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("status")).toContainText("Your session was deleted");
  });

  test("branding: the header shows the BHP logo and the names the server is configured with", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("banner").getByRole("img", { name: "BHP" })).toBeVisible();
    const logo = await page.getByRole("banner").getByRole("img", { name: "BHP" }).evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0);
    expect(logo).toBe(true); // the logo file is really served
    await expect(page.getByRole("banner")).toContainText("SCP AI Ideation assistant"); // the default; set HEADER_TEXT to change it
    await expect(page.getByRole("contentinfo")).toContainText("BHP");
    await expect(page).toHaveTitle("SCP AI Ideation assistant");
    const names = await page.request.get("/api/branding").then((r) => r.json());
    expect(Object.keys(names).sort()).toEqual(["app_name", "footer_text", "header_text"]);
  });

  test("prints to a single dark landscape page", async ({ page }) => {
    await signInAndWait(page);
    await page.route("**/api/conversation", (route) => route.fulfill({ json: READY }));
    await page.goto("/review");
    await expect(page.locator(".one-pager")).toBeVisible();
    await page.emulateMedia({ media: "print" });
    await expect(page.locator(".review-actions")).toBeHidden(); // buttons and chrome are not printed
    await expect(page.locator(".readback")).toBeHidden();
    await expect(page.locator("header.layout__header")).toBeHidden();
    const pdf = await page.pdf({ preferCSSPageSize: true, printBackground: true });
    expect((pdf.toString("latin1").match(/\/Type\s*\/Page[^s]/g) ?? []).length).toBe(1);
  });

  test("an incomplete canvas cannot be generated, and the page needs a session", async ({ page, browser }) => {
    await signInAndWait(page);
    await page.goto("/review");
    await expect(page.getByRole("heading", { name: "Your canvas is not complete yet" })).toBeVisible();
    await expect(page.locator(".one-pager")).toHaveCount(0);

    const other = await browser.newContext();
    const anonymous = await other.newPage();
    await anonymous.goto("/review");
    await expect(anonymous.getByRole("heading", { name: "Enter the access code" })).toBeVisible();
    await expect(anonymous.locator(".one-pager")).toHaveCount(0);
    await other.close();
  });
});
