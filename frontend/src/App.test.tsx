import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { SESSION, csrfRoute, envelope, json, mockApi } from "./test/mockApi";

const CID = "fedcba9876543210fedcba9876543210";
const STATUS = () => json({ status: "ok", environment: "test" });
const SIGNED_IN = () => json(SESSION);

afterEach(() => vi.unstubAllGlobals());

describe("App when signed in", () => {
  it("renders the workspace and shows loading, then the API result", async () => {
    mockApi({ "GET /api/session": SIGNED_IN, "GET /api/status": STATUS });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Plan on a Page", level: 1 })).toBeInTheDocument();
    expect(await screen.findByTestId("status-result")).toHaveTextContent("Service is ok (test).");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Access code")).not.toBeInTheDocument();
  });

  it("shows only a generic message and a reference when the API fails", async () => {
    mockApi({
      "GET /api/session": SIGNED_IN,
      "GET /api/status": () => envelope("internal_error", 500, CID),
    });
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).toHaveTextContent(CID);
    expect(document.body).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
  });

  it("recovers when the user retries", async () => {
    const { fn } = mockApi({
      "GET /api/session": SIGNED_IN,
      "GET /api/status": [() => envelope("internal_error", 500, CID), STATUS],
    });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("status-result")).toBeInTheDocument();
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("signing out ends the session and returns to the access screen", async () => {
    const { calls } = mockApi({
      "GET /api/session": SIGNED_IN,
      "GET /api/status": STATUS,
      "GET /api/csrf": csrfRoute,
      "DELETE /api/session": () => new Response(null, { status: 204 }),
    });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(screen.queryByTestId("status-result")).not.toBeInTheDocument();
    const del = calls.find((c) => c.key === "DELETE /api/session")!;
    expect((del.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("test-csrf-token");
  });
});

describe("App when not signed in", () => {
  it("shows the access screen and none of the workspace", async () => {
    mockApi({ "GET /api/session": () => envelope("unauthenticated", 401) });
    render(<App />);
    expect(await screen.findByLabelText("Access code")).toBeInTheDocument();
    expect(screen.queryByText(/Complete the AI Opportunity Canvas/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
  });

  it("shows a generic message, not the access screen, when the session check itself fails", async () => {
    mockApi({ "GET /api/session": () => envelope("service_unavailable", 503, CID) });
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).toHaveTextContent(CID);
    expect(screen.queryByLabelText("Access code")).not.toBeInTheDocument();
  });

  it("shows a generic message when the network is down", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).not.toHaveTextContent("Reference");
  });
});
