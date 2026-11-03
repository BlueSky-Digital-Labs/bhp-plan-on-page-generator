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
