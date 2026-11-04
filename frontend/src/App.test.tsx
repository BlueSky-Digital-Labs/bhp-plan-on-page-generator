import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { SESSION, csrfRoute, envelope, json, mockApi } from "./test/mockApi";

// Building the PDF needs a real canvas and layout engine; the real thing is covered by the browser tests.
const downloadPdf = vi.hoisted(() => vi.fn<(el: HTMLElement, filename: string) => Promise<void>>());
vi.mock("./canvas/downloadPdf", () => ({ downloadOnePagerPdf: downloadPdf }));

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

describe("entry route /", () => {
  it("shows the access form to a visitor without access, and nothing protected", async () => {
    mockApi({ "GET /api/access-state": NOT_PERMITTED });
    render(<App />);
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(startButton()).not.toBeInTheDocument();
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
    const { fn } = mockApi({
      "GET /api/access-state": [() => envelope("service_unavailable", 503, CID), NOT_PERMITTED],
    });
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).toHaveTextContent(CID);
    expect(screen.queryByLabelText("Access code")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(fn).toHaveBeenCalledTimes(2);
  });
});

describe("protected route /start", () => {
  it("shows no protected content while the backend is still being asked", async () => {
    let answer!: (r: Response) => void;
    const pending = new Promise<Response>((resolve) => (answer = resolve));
    mockApi({ "GET /api/access-state": () => pending });
    at("/start");
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking access");
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

describe("review route (the one-page canvas)", () => {
  const READY = {
    revision: 3,
    turns: [],
    canvas: { fields: { solutionName: "Shift Notes Assistant", team: "Ops", priority: "High", opportunityTypes: ["Analyse Data"] }, missing_required: [], ready_for_review: true },
  };
  const NOT_READY = { revision: 1, turns: [], canvas: { fields: {}, missing_required: ["team", "priority"], ready_for_review: false } };

  it("shows the one-pager only when the server says the canvas is ready, and can download it as a PDF", async () => {
    downloadPdf.mockResolvedValue(undefined);
    mockApi({ "GET /api/session": SIGNED_IN, "GET /api/conversation": () => json(READY) });
    at("/review");
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: /AI OPPORTUNITY CANVAS/ })).toBeInTheDocument();
    expect(screen.getByText("Shift Notes Assistant")).toBeInTheDocument();
    await chooseDownload("Download canvas", "PDF");
    expect(downloadPdf).toHaveBeenCalledTimes(1);
    expect(downloadPdf.mock.calls[0][0]).toHaveClass("one-pager"); // the rendered canvas, not anything else
    expect(downloadPdf.mock.calls[0][1]).toBe("plan-on-a-page.pdf");
    expect(screen.getByText(/Open a file to check it/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says so, and lets the user try again, when the PDF cannot be created", async () => {
    downloadPdf.mockRejectedValueOnce(new Error("canvas failed")).mockResolvedValueOnce(undefined);
    mockApi({ "GET /api/session": SIGNED_IN, "GET /api/conversation": () => json(READY) });
    at("/review");
    render(<App />);
    await chooseDownload("Download canvas", "PDF");
    expect(await screen.findByRole("alert")).toHaveTextContent("The PDF could not be created");
    expect(screen.queryByText(/canvas failed/)).not.toBeInTheDocument(); // no internal detail shown
    await chooseDownload("Download canvas", "PDF");
    expect(downloadPdf).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  describe("PowerPoint canvas", () => {
    let saved: Array<{ download: string }>;
    beforeEach(() => {
      saved = [];
      vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:test", revokeObjectURL: () => {} }));
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
        saved.push({ download: this.download });
      });
    });
    const open = async (file: () => Response) => {
      const api = mockApi({
        "GET /api/session": SIGNED_IN,
        "GET /api/conversation": () => json(READY),
        "GET /api/canvas-file?format=pptx": file,
      });
      at("/review");
      render(<App />);
      await screen.findByRole("button", { name: /^Download canvas/ });
      return api;
    };

    it("downloads the canvas as a PowerPoint slide under a fixed name", async () => {
      const { calls } = await open(() => new Response(new Blob(["x"]), { status: 200 }));
      await chooseDownload("Download canvas", "PowerPoint (.pptx)");
      await waitFor(() => expect(saved).toEqual([{ download: "plan-on-a-page.pptx" }]));
      expect(calls.some((c) => c.key === "GET /api/canvas-file?format=pptx")).toBe(true);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("explains a refusal in fixed words, and any other failure generically", async () => {
      await open(() => envelope("canvas_not_ready", 409));
      await chooseDownload("Download canvas", "PowerPoint (.pptx)");
      expect(await screen.findByRole("alert")).toHaveTextContent("The canvas is not complete yet");
      expect(screen.queryByText(/SERVER-TEXT-MUST-NEVER-RENDER/)).not.toBeInTheDocument();
    });

    it("says it could not be created when the server fails, and can be retried", async () => {
      await open(() => envelope("canvas_failed", 500));
      await chooseDownload("Download canvas", "PowerPoint (.pptx)");
      expect(await screen.findByRole("alert")).toHaveTextContent("The PowerPoint file could not be created");
      expect(screen.getByRole("button", { name: /^Download canvas/ })).toBeEnabled();
    });

    it("returns to entry when the session has ended", async () => {
      await open(() => envelope("unauthenticated", 401));
      await chooseDownload("Download canvas", "PowerPoint (.pptx)");
      await waitFor(() => expect(window.location.pathname).toBe("/"));
    });
  });

  describe("appendix", () => {
    const file = () => new Response(new Blob(["bytes"]), { status: 200, headers: { "Content-Type": "application/pdf" } });
    let saved: Array<{ download: string; href: string }>;

    beforeEach(() => {
      saved = [];
      vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:test", revokeObjectURL: () => {} }));
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
        saved.push({ download: this.download, href: this.href });
      });
    });

    const open = async (appendix: () => Response | Promise<Response>) => {
      const api = mockApi({
        "GET /api/session": SIGNED_IN,
        "GET /api/conversation": () => json(READY),
        "GET /api/appendix?format=pdf": appendix,
        "GET /api/appendix?format=docx": appendix,
        "GET /api/appendix?format=json": appendix,
      });
      at("/review");
      render(<App />);
      await screen.findByRole("button", { name: /^Download appendix/ });
      return api;
    };

    it.each([
      ["PDF", "pdf"],
      ["Word (.docx)", "docx"],
      ["JSON", "json"],
    ])("downloads the appendix as %s under a fixed file name", async (label, format) => {
      const { calls } = await open(file);
      await chooseDownload("Download appendix", label);
      await waitFor(() => expect(saved).toHaveLength(1));
      expect(saved[0].download).toBe(`plan-on-a-page-appendix.${format}`);
      expect(calls.some((c) => c.key === `GET /api/appendix?format=${format}`)).toBe(true);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("explains a refusal in fixed words and never shows what the server said", async () => {
      await open(() => envelope("canvas_not_ready", 409));
      await chooseDownload("Download appendix", "PDF");
      expect(await screen.findByRole("alert")).toHaveTextContent("The canvas is not complete yet");
      expect(screen.queryByText(/SERVER-TEXT-MUST-NEVER-RENDER/)).not.toBeInTheDocument();
      expect(saved).toHaveLength(0);
    });

    it("says it could not be created for any other failure, and can be retried", async () => {
      await open(() => envelope("appendix_failed", 500));
      await chooseDownload("Download appendix", "Word (.docx)");
      expect(await screen.findByRole("alert")).toHaveTextContent("The appendix could not be created");
      expect(screen.getByRole("button", { name: /^Download appendix/ })).toBeEnabled();
    });

    it("returns to entry when the session has ended", async () => {
      await open(() => envelope("unauthenticated", 401));
      await chooseDownload("Download appendix", "JSON");
      await waitFor(() => expect(window.location.pathname).toBe("/"));
    });

    it("only offers one download at a time", async () => {
      let release!: () => void;
      await open(() => new Promise<Response>((resolve) => (release = () => resolve(file()))));
      await chooseDownload("Download appendix", "PDF");
      expect(screen.getByRole("button", { name: "Preparing…" })).toBeDisabled(); // one download at a time
      release();
      await waitFor(() => expect(screen.getByRole("button", { name: /^Download appendix/ })).toBeEnabled());
    });

    it("is not offered while the canvas is incomplete", async () => {
      mockApi({ "GET /api/session": SIGNED_IN, "GET /api/conversation": () => json(NOT_READY) });
      at("/review");
      render(<App />);
      await screen.findByText("Your canvas is not complete yet");
      expect(screen.queryByRole("button", { name: /Download appendix/ })).not.toBeInTheDocument();
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
