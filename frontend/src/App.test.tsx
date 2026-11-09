import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { SESSION, csrfRoute, envelope, json, mockApi } from "./test/mockApi";

const CID = "fedcba9876543210fedcba9876543210";
const PERMITTED = () => json({ start_permitted: true });
const NOT_PERMITTED = () => json({ start_permitted: false });
const SIGNED_IN = () => json(SESSION);
const STATUS = () => json({ status: "ok", environment: "test" });
const EMPTY_CONVERSATION = () => json({ revision: 0, turns: [] });
const STARTED = (next = "/conversation") => () => json({ ...SESSION, next }, 201);

function at(path: string) {
  window.history.replaceState(null, "", path);
}

// Opens a download menu and picks a format, as a user would.
async function chooseDownload(menu: string, item: string) {
  await userEvent.click(await screen.findByRole("button", { name: new RegExp(`^${menu}`) }));
  await userEvent.click(await screen.findByRole("menuitem", { name: item }));
}

beforeEach(() => at("/"));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  at("/");
});

const startButton = () => screen.queryByRole("button", { name: "Start a new canvas" });

describe("configured names", () => {
  const configured = () =>
    json({ app_name: "Integrated Operations Plan on a Page", header_text: "Integrated Operations", footer_text: "BHP · Integrated Operations" });

  it("shows the configured name in the header, the entry heading, the footer and the browser tab, with the BHP logo", async () => {
    mockApi({ "GET /api/branding": configured, "GET /api/access-state": NOT_PERMITTED });
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Integrated Operations Plan on a Page" })).toBeInTheDocument();
    expect(screen.getByRole("banner")).toHaveTextContent("Integrated Operations");
    expect(screen.getByRole("contentinfo")).toHaveTextContent("BHP · Integrated Operations");
    expect(screen.getByRole("img", { name: "BHP" })).toHaveAttribute("src", "/bhp-logo.png");
    expect(document.title).toBe("Integrated Operations Plan on a Page");
  });

  it("uses the default names if they cannot be read, and the app still works", async () => {
    mockApi({ "GET /api/branding": () => envelope("service_unavailable", 503), "GET /api/access-state": NOT_PERMITTED });
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "SCP AI Ideation assistant" })).toBeInTheDocument();
    expect(screen.getByLabelText("Access code")).toBeInTheDocument();
    expect(document.title).toBe("SCP AI Ideation assistant");
  });

  it("shows a configured name as plain text, never as markup", async () => {
    mockApi({
      "GET /api/branding": () => json({ app_name: "<img src=x onerror=alert(1)>", header_text: "<b>Ops</b>", footer_text: "BHP" }),
      "GET /api/access-state": NOT_PERMITTED,
    });
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(document.querySelector("header b, main img")).toBeNull();
  });
});

describe("entry route /", () => {
  it("shows the access form to a visitor without access, and nothing protected", async () => {
    mockApi({ "GET /api/access-state": NOT_PERMITTED });
    render(<App />);
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(startButton()).not.toBeInTheDocument();
  });

  it("tells someone whose session was stopped halfway how to carry on, and shows nothing from it", async () => {
    mockApi({ "GET /api/access-state": NOT_PERMITTED });
    window.history.replaceState({ notice: "session_stopped" }, "", "/");
    render(<App />);
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Your session was stopped and everything on the server was deleted. To carry on, enter the access code, start a new session and attach the file you downloaded to your first answer.",
    );
  });

  it("sends a browser that already has access straight to /start", async () => {
    mockApi({ "GET /api/access-state": PERMITTED });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Start a new canvas" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/start");
  });

  it("moves to /start after a correct code", async () => {
    mockApi({
      "GET /api/access-state": [NOT_PERMITTED, PERMITTED],
      "GET /api/csrf": csrfRoute,
      "POST /api/access": () => json({ status: "ok" }),
    });
    render(<App />);
    await userEvent.type(await screen.findByLabelText("Access code"), "right-code{Enter}");
    expect(await screen.findByRole("button", { name: "Start a new canvas" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/start");
  });

  it("keeps the visitor on entry after a wrong code, with generic feedback", async () => {
    mockApi({
      "GET /api/access-state": NOT_PERMITTED,
      "GET /api/csrf": csrfRoute,
      "POST /api/access": () => envelope("invalid_access_code", 401),
    });
    render(<App />);
    await userEvent.type(await screen.findByLabelText("Access code"), "wrong{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("not recognised");
    expect(window.location.pathname).toBe("/");
    expect(startButton()).not.toBeInTheDocument();
  });

  it("shows a safe, retryable error if the access check fails", async () => {
    const { calls } = mockApi({
      "GET /api/access-state": [() => envelope("service_unavailable", 503, CID), NOT_PERMITTED],
    });
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).toHaveTextContent(CID);
    expect(screen.queryByLabelText("Access code")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(calls).toHaveLength(2);
  });
});

describe("protected route /start", () => {
  it("shows no protected content while the backend is still being asked", async () => {
    let answer!: (r: Response) => void;
    const pending = new Promise<Response>((resolve) => (answer = resolve));
    mockApi({ "GET /api/access-state": () => pending });
    at("/start");
    render(<App />);
    expect(await screen.findByRole("status")).toHaveTextContent("Checking access");
    expect(startButton()).not.toBeInTheDocument();
    expect(screen.queryByText(/What you will do/)).not.toBeInTheDocument();
    answer(PERMITTED());
    expect(await screen.findByRole("button", { name: "Start a new canvas" })).toBeInTheDocument();
  });

  it("on a direct visit without access, returns to entry with neutral guidance", async () => {
    mockApi({ "GET /api/access-state": NOT_PERMITTED });
    at("/start");
    render(<App />);
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
    expect(screen.getByRole("status")).toHaveTextContent("Your access has expired or has not been entered");
    expect(startButton()).not.toBeInTheDocument();
  });

  it("loads on refresh and verifies access with the backend, not prior client state", async () => {
    const { calls } = mockApi({ "GET /api/access-state": PERMITTED });
    at("/start");
    render(<App />);
    expect(await screen.findByRole("button", { name: "Start a new canvas" })).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toEqual(["GET /api/access-state"]);
  });

  it("starts a session and follows the server's route to the conversation", async () => {
    mockApi({
      "GET /api/access-state": PERMITTED,
      "GET /api/csrf": csrfRoute,
      "POST /api/sessions": STARTED(),
      "GET /api/session": SIGNED_IN,
      "GET /api/conversation": EMPTY_CONVERSATION,
    });
    at("/start");
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Start a new canvas" }));
    expect(await screen.findByLabelText("Describe your idea")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/conversation");
  });

  it("never navigates off-site even if the server response names another destination", async () => {
    mockApi({
      "GET /api/access-state": PERMITTED,
      "GET /api/csrf": csrfRoute,
      "POST /api/sessions": STARTED("https://evil.example/steal"),
      "GET /api/session": SIGNED_IN,
      "GET /api/conversation": EMPTY_CONVERSATION,
    });
    at("/start");
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Start a new canvas" }));
    await waitFor(() => expect(window.location.pathname).toBe("/conversation"));
    expect(window.location.hostname).not.toBe("evil.example");
  });

  it("returns to entry if access expires between the check and the click", async () => {
    mockApi({
      "GET /api/access-state": [PERMITTED, NOT_PERMITTED],
      "GET /api/csrf": csrfRoute,
      "POST /api/sessions": () => envelope("unauthenticated", 401),
    });
    at("/start");
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Start a new canvas" }));
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });
});

describe("conversation route", () => {
  it("requires a real session: without one it returns to entry", async () => {
    mockApi({
      "GET /api/session": () => envelope("unauthenticated", 401),
      "GET /api/access-state": NOT_PERMITTED,
    });
    at("/conversation");
    render(<App />);
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(screen.queryByLabelText("Describe your idea")).not.toBeInTheDocument();
  });

  it("shows the conversation for a valid session, and signing out returns to entry", async () => {
    mockApi({
      "GET /api/session": SIGNED_IN,
      "GET /api/conversation": EMPTY_CONVERSATION,
      "GET /api/csrf": csrfRoute,
      "DELETE /api/session": () => new Response(null, { status: 204 }),
      "GET /api/access-state": NOT_PERMITTED,
    });
    at("/conversation");
    render(<App />);
    expect(await screen.findByLabelText("Describe your idea")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });
});

describe("review route (read-back, confirmation and downloads)", () => {
  const FIELDS = { solutionName: "Shift Notes Assistant", team: "Ops", priority: "Must-have", opportunityTypes: ["Analyse Data"] };
  const review = (revision: number, confirmed: number | null) => () =>
    json({
      revision,
      confirmed_revision: confirmed,
      turns: [],
      canvas: { fields: FIELDS, missing_required: [], ready_for_review: true },
    });
  const NOT_READY = { revision: 1, turns: [], canvas: { fields: {}, missing_required: ["team", "priority"], ready_for_review: false } };
  const file = (name = "shift-notes-assistant-canvas-r3.pdf") => () =>
    new Response(new Blob(["bytes"]), { status: 200, headers: { "Content-Disposition": `attachment; filename="${name}"` } });
  let saved: string[];

  beforeEach(() => {
    saved = [];
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:test", revokeObjectURL: () => {} }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download);
    });
  });

  // The server's download status for the review page; nothing downloaded unless a test says so.
  const downloads =
    (canvas = false, appendix = false, revision = 3) =>
    () =>
      json({
        revision,
        canvas_downloaded: canvas,
        appendix_downloaded: appendix,
        purge_eligible: canvas, // the one-page canvas alone unlocks deleting
        lock_reason: canvas ? null : "canvas_not_downloaded",
      });
  const open = async (routes: Parameters<typeof mockApi>[0]) => {
    const api = mockApi({ "GET /api/session": SIGNED_IN, "GET /api/csrf": csrfRoute, "GET /api/downloads": downloads(), ...routes });
    at("/review");
    render(<App />);
    await screen.findByRole("heading", { level: 2, name: "Check your canvas" });
    return api;
  };
  const menus = () => screen.queryAllByRole("button", { name: /^Download (canvas|appendix)/ });

  it("reads the whole canvas back with the user's priority, and offers no downloads before it is confirmed", async () => {
    await open({ "GET /api/conversation": review(3, null) });
    expect(screen.getByRole("heading", { level: 1, name: /AI OPPORTUNITY CANVAS/ })).toBeInTheDocument();
    expect(screen.getByText("Shift Notes Assistant")).toBeInTheDocument();
    const readBack = screen.getByRole("heading", { name: "Check your canvas" }).parentElement!;
    expect(readBack).toHaveTextContent("and for the priority, Must-have, which is the one you chose");
    expect(screen.getByRole("button", { name: "Yes, this is correct" })).toBeInTheDocument();
    expect(menus()).toHaveLength(0);
  });

  it("sends the displayed revision when the user confirms, then shows the downloads", async () => {
    const { calls } = await open({
      "GET /api/conversation": review(3, null),
      "POST /api/canvas/confirm": review(3, 3),
    });
    await userEvent.click(screen.getByRole("button", { name: "Yes, this is correct" }));
    expect(await screen.findByRole("status")).toHaveTextContent("You confirmed this version");
    expect(JSON.parse(String(calls.find((c) => c.key === "POST /api/canvas/confirm")!.init?.body))).toEqual({ revision: 3 });
    expect(screen.getByRole("button", { name: /^Download canvas/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Download appendix/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Yes, this is correct" })).not.toBeInTheDocument();
  });

  it("shows the downloads straight away for a revision that was already confirmed", async () => {
    await open({ "GET /api/conversation": review(3, 3) });
    expect(menus()).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Yes, this is correct" })).not.toBeInTheDocument();
  });

  it("does not treat a confirmation of an earlier revision as current", async () => {
    await open({ "GET /api/conversation": review(4, 3) }); // answered again since confirming
    expect(screen.getByRole("button", { name: "Yes, this is correct" })).toBeInTheDocument();
    expect(menus()).toHaveLength(0);
  });

  it("offers no way back to the conversation once the canvas is confirmed", async () => {
    await open({ "GET /api/conversation": review(3, 3) });
    expect(screen.queryByRole("button", { name: /Back to the conversation/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /go back and correct it/ })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("so it is final");
  });

  it("lets the user go back to correct something through the conversation", async () => {
    await open({ "GET /api/conversation": review(3, null) });
    await userEvent.click(screen.getByRole("button", { name: "Something is wrong: go back and correct it" }));
    expect(window.location.pathname).toBe("/conversation");
  });

  it("re-reads the canvas when it changed before the user confirmed, and does not claim a confirmation", async () => {
    await open({
      "GET /api/conversation": [review(3, null), review(4, null)],
      "POST /api/canvas/confirm": () => envelope("revision_conflict", 409),
    });
    await userEvent.click(screen.getByRole("button", { name: "Yes, this is correct" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The canvas changed since it was shown");
    expect(screen.queryByText(/SERVER-TEXT-MUST-NEVER-RENDER/)).not.toBeInTheDocument();
    expect(menus()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Yes, this is correct" })).toBeEnabled();
  });

  it("shows the incomplete screen if the canvas is no longer complete when confirming", async () => {
    await open({
      "GET /api/conversation": [review(3, null), () => json(NOT_READY)],
      "POST /api/canvas/confirm": () => envelope("canvas_incomplete", 422),
    });
    await userEvent.click(screen.getByRole("button", { name: "Yes, this is correct" }));
    expect(await screen.findByRole("heading", { name: "Your canvas is not complete yet" })).toBeInTheDocument();
  });

  it("returns to entry if the session ended while confirming", async () => {
    await open({ "GET /api/conversation": review(3, null), "POST /api/canvas/confirm": () => envelope("unauthenticated", 401) });
    await userEvent.click(screen.getByRole("button", { name: "Yes, this is correct" }));
    await waitFor(() => expect(window.location.pathname).toBe("/"));
  });

  it.each([
    ["canvas", "PDF", "GET /api/canvas-file?format=pdf&revision=3", "shift-notes-assistant-canvas-r3.pdf"],
    ["canvas", "PowerPoint (.pptx)", "GET /api/canvas-file?format=pptx&revision=3", "shift-notes-assistant-canvas-r3.pptx"],
    ["appendix", "PDF", "GET /api/appendix?format=pdf&revision=3", "shift-notes-assistant-appendix-r3.pdf"],
    ["appendix", "Word (.docx)", "GET /api/appendix?format=docx&revision=3", "shift-notes-assistant-appendix-r3.docx"],
    ["appendix", "JSON", "GET /api/appendix?format=json&revision=3", "shift-notes-assistant-appendix-r3.json"],
  ])("downloads the %s as %s for the confirmed revision, under the server's file name", async (menu, item, key, name) => {
    const { calls } = await open({ "GET /api/conversation": review(3, 3), [key]: file(name) });
    await chooseDownload(`Download ${menu}`, item);
    await waitFor(() => expect(saved).toEqual([name]));
    expect(calls.some((c) => c.key === key)).toBe(true);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("uses a fixed name if the server's file name is not a plain one", async () => {
    await open({
      "GET /api/conversation": review(3, 3),
      "GET /api/canvas-file?format=pdf&revision=3": file("../../evil name.pdf"),
    });
    await chooseDownload("Download canvas", "PDF");
    await waitFor(() => expect(saved).toEqual(["plan-on-a-page.pdf"]));
  });

  it("re-reads and drops the downloads when the server says the confirmation no longer applies", async () => {
    await open({
      "GET /api/conversation": [review(3, 3), review(4, 3)],
      "GET /api/canvas-file?format=pdf&revision=3": () => envelope("revision_conflict", 409),
    });
    await chooseDownload("Download canvas", "PDF");
    expect(await screen.findByRole("alert")).toHaveTextContent("The canvas changed since it was shown");
    await waitFor(() => expect(menus()).toHaveLength(0)); // no stale eligibility left on screen
    expect(screen.getByRole("button", { name: "Yes, this is correct" })).toBeInTheDocument();
    expect(saved).toHaveLength(0);
  });

  it("says it could not be done for any other failure and can be retried", async () => {
    await open({
      "GET /api/conversation": review(3, 3),
      "GET /api/appendix?format=pdf&revision=3": () => envelope("appendix_failed", 500),
    });
    await chooseDownload("Download appendix", "PDF");
    expect(await screen.findByRole("alert")).toHaveTextContent("That could not be done. Please try again.");
    expect(screen.getByRole("button", { name: /^Download appendix/ })).toBeEnabled();
    expect(saved).toHaveLength(0);
  });

  it("returns to entry when the session ended during a download", async () => {
    await open({
      "GET /api/conversation": review(3, 3),
      "GET /api/appendix?format=json&revision=3": () => envelope("unauthenticated", 401),
    });
    await chooseDownload("Download appendix", "JSON");
    await waitFor(() => expect(window.location.pathname).toBe("/"));
  });

  it("only runs one canvas download at a time", async () => {
    let release!: () => void;
    await open({
      "GET /api/conversation": review(3, 3),
      "GET /api/canvas-file?format=pdf&revision=3": () => new Promise<Response>((resolve) => (release = () => resolve(file()()))),
    });
    await chooseDownload("Download canvas", "PDF");
    expect(screen.getByRole("button", { name: "Preparing…" })).toBeDisabled();
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: /^Download canvas/ })).toBeEnabled());
  });

  describe("download status and deleting", () => {
    const statusCard = () => screen.queryByRole("heading", { level: 2, name: "Your documents" });
    const deleteButton = () => screen.queryByRole("button", { name: /^Delete my session/ });
    const reads = (calls: Array<{ key: string }>) => calls.filter((c) => c.key === "GET /api/downloads").length;

    it("shows no download status before the canvas is confirmed", async () => {
      await open({ "GET /api/conversation": review(3, null) });
      expect(statusCard()).not.toBeInTheDocument();
    });

    it("shows the two documents for the confirmed revision, with deleting locked", async () => {
      await open({ "GET /api/conversation": review(3, 3) });
      expect(await screen.findByRole("heading", { level: 2, name: "Your documents" })).toBeInTheDocument();
      expect(await screen.findByText(/Not downloaded yet/)).toBeInTheDocument();
      expect(deleteButton()).toBeDisabled();
    });

    it("shows the status after confirming, for the revision just confirmed", async () => {
      const { calls } = await open({
        "GET /api/conversation": review(3, null),
        "POST /api/canvas/confirm": review(3, 3),
      });
      await userEvent.click(screen.getByRole("button", { name: "Yes, this is correct" }));
      expect(await screen.findByRole("heading", { level: 2, name: "Your documents" })).toBeInTheDocument();
      await waitFor(() => expect(reads(calls)).toBe(1));
    });

    it("reads the status again after a download attempt, and unlocks only when the server says so", async () => {
      const { calls } = await open({
        "GET /api/conversation": review(3, 3),
        "GET /api/downloads": [downloads(false, false), downloads(true, true)],
        "GET /api/canvas-file?format=pdf&revision=3": file(),
      });
      await screen.findByText(/Not downloaded yet/);
      expect(deleteButton()).toBeDisabled();
      await chooseDownload("Download canvas", "PDF");
      await waitFor(() => expect(saved).toHaveLength(1));
      await waitFor(() => expect(deleteButton()).toBeEnabled());
      expect(reads(calls)).toBeGreaterThanOrEqual(2);
    });

    it("does not unlock deleting just because a download was clicked", async () => {
      await open({
        "GET /api/conversation": review(3, 3),
        "GET /api/downloads": downloads(false, false), // the server has not finished sending anything
        "GET /api/canvas-file?format=pdf&revision=3": file(),
      });
      await screen.findByText(/Not downloaded yet/);
      await chooseDownload("Download canvas", "PDF");
      await waitFor(() => expect(saved).toHaveLength(1));
      await waitFor(() => expect(screen.getByText(/Not downloaded yet/)).toBeInTheDocument());
      expect(deleteButton()).toBeDisabled();
    });

    it("re-reads the canvas when the status belongs to a newer revision", async () => {
      await open({
        "GET /api/conversation": [review(3, 3), review(4, null)],
        "GET /api/downloads": downloads(true, true, 4), // answered again in another tab
      });
      expect(await screen.findByRole("alert")).toHaveTextContent("The canvas changed since it was shown");
      await waitFor(() => expect(screen.getByRole("button", { name: "Yes, this is correct" })).toBeInTheDocument());
      expect(statusCard()).not.toBeInTheDocument();
      expect(deleteButton()).not.toBeInTheDocument();
    });

    it("deletes after confirmation, then returns to entry saying the session was deleted", async () => {
      const { calls } = await open({
        "GET /api/conversation": review(3, 3),
        "GET /api/downloads": downloads(true, true),
        "POST /api/session/purge": () => new Response(null, { status: 204 }),
        "GET /api/access-state": () => json({ start_permitted: false }),
      });
      await userEvent.click(await screen.findByRole("button", { name: /^Delete my session/ }));
      await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
      await waitFor(() => expect(window.location.pathname).toBe("/"));
      expect(JSON.parse(String(calls.find((c) => c.key === "POST /api/session/purge")!.init?.body))).toEqual({ revision: 3 });
      expect(await screen.findByRole("status")).toHaveTextContent("Your session was deleted");
    });

    it("keeps the user on the page, with fixed guidance, if the server refuses", async () => {
      await open({
        "GET /api/conversation": review(3, 3),
        "GET /api/downloads": downloads(true, true),
        "POST /api/session/purge": () => envelope("purge_locked", 409),
      });
      await userEvent.click(await screen.findByRole("button", { name: /^Delete my session/ }));
      await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Deleting is still locked");
      expect(window.location.pathname).toBe("/review");
    });
  });

  describe("submitting by email", () => {
    const mailButton = () => screen.queryByRole("button", { name: /^(Submit by email|Submitting…)$/ });

    it("offers no email before the canvas is confirmed", async () => {
      await open({ "GET /api/conversation": review(3, null) });
      expect(mailButton()).not.toBeInTheDocument();
    });

    it("sends nothing until the button is clicked, and asks for nothing first", async () => {
      const { calls } = await open({ "GET /api/conversation": review(3, 3) });
      expect(mailButton()).toBeInTheDocument();
      expect(screen.queryByLabelText("To")).not.toBeInTheDocument();
      expect(calls.some((c) => c.key === "POST /api/email")).toBe(false);
    });

    it("submits the confirmed revision in one click and says it was sent", async () => {
      const { calls } = await open({
        "GET /api/conversation": review(3, 3),
        "POST /api/email": () => json({ sent: 1 }),
      });
      await userEvent.click(mailButton()!);
      expect(await screen.findByText(/^Submitted by email\./)).toBeInTheDocument();
      expect(JSON.parse(String(calls.find((c) => c.key === "POST /api/email")!.init?.body))).toEqual({ revision: 3 });
    });

    it("re-reads the canvas and drops the email and download buttons when the confirmation no longer applies", async () => {
      await open({
        "GET /api/conversation": [review(3, 3), review(4, 3)],
        "POST /api/email": () => envelope("revision_conflict", 409),
      });
      await userEvent.click(mailButton()!);
      expect(await screen.findByRole("alert")).toHaveTextContent("The canvas changed since it was shown");
      await waitFor(() => expect(mailButton()).not.toBeInTheDocument());
      expect(menus()).toHaveLength(0);
      expect(screen.getByRole("button", { name: "Yes, this is correct" })).toBeInTheDocument();
    });

    it("returns to entry when the session ended while sending", async () => {
      await open({
        "GET /api/conversation": review(3, 3),
        "POST /api/email": () => envelope("unauthenticated", 401),
      });
      await userEvent.click(mailButton()!);
      await waitFor(() => expect(window.location.pathname).toBe("/"));
    });
  });

  it("does not render a canvas that is not complete, and explains what is left", async () => {
    mockApi({ "GET /api/session": SIGNED_IN, "GET /api/conversation": () => json(NOT_READY) });
    at("/review");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Your canvas is not complete yet" })).toBeInTheDocument();
    expect(screen.getByText(/2 required answers are still to go/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /AI OPPORTUNITY CANVAS/ })).not.toBeInTheDocument();
  });

  it("requires a session: without one it returns to entry and shows no canvas", async () => {
    mockApi({ "GET /api/session": () => envelope("unauthenticated", 401), "GET /api/access-state": NOT_PERMITTED });
    at("/review");
    render(<App />);
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(screen.queryByText(/AI OPPORTUNITY CANVAS/)).not.toBeInTheDocument();
  });

  it("shows a safe, retryable message if the canvas cannot be loaded", async () => {
    mockApi({ "GET /api/session": SIGNED_IN, "GET /api/conversation": () => envelope("internal_error", 500, CID) });
    at("/review");
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).toHaveTextContent(CID);
  });
});

describe("canvas route (the original template form)", () => {
  it("still works for a signed-in session", async () => {
    mockApi({ "GET /api/session": SIGNED_IN, "GET /api/status": STATUS });
    at("/canvas");
    render(<App />);
    expect(await screen.findByTestId("status-result")).toHaveTextContent("Service is ok (test).");
    expect(screen.getByText(/Complete the AI Opportunity Canvas/)).toBeInTheDocument();
  });

  it("shows only a generic message and a reference when the status API fails", async () => {
    mockApi({
      "GET /api/session": SIGNED_IN,
      "GET /api/status": () => envelope("internal_error", 500, CID),
    });
    at("/canvas");
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).toHaveTextContent(CID);
    expect(document.body).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
  });
});

describe("routing and browser storage", () => {
  it("sends an unknown path to the entry route", async () => {
    mockApi({ "GET /api/access-state": NOT_PERMITTED });
    at("/somewhere-else");
    render(<App />);
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });

  it("writes nothing to localStorage or sessionStorage across the whole flow", async () => {
    const local = vi.spyOn(Storage.prototype, "setItem");
    mockApi({
      "GET /api/access-state": [NOT_PERMITTED, PERMITTED],
      "GET /api/csrf": csrfRoute,
      "POST /api/access": () => json({ status: "ok" }),
      "POST /api/sessions": STARTED(),
      "GET /api/session": SIGNED_IN,
      "GET /api/conversation": EMPTY_CONVERSATION,
    });
    render(<App />);
    await userEvent.type(await screen.findByLabelText("Access code"), "right-code{Enter}");
    await userEvent.click(await screen.findByRole("button", { name: "Start a new canvas" }));
    await screen.findByLabelText("Describe your idea");
    expect(local).not.toHaveBeenCalled();
    expect(localStorage.length + sessionStorage.length).toBe(0);
  });
});
