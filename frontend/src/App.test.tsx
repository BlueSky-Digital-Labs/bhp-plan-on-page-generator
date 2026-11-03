import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const CID = "fedcba9876543210fedcba9876543210";

function respondWith(...responses: Array<() => Response>) {
  const queue = [...responses];
  const fn = vi.fn(async () => (queue.length > 1 ? queue.shift()! : queue[0])());
  vi.stubGlobal("fetch", fn);
  return fn;
}

const ok = () =>
  new Response(JSON.stringify({ status: "ok", environment: "test" }), {
    headers: { "Content-Type": "application/json" },
  });

const leakyFailure = () =>
  new Response(
    JSON.stringify({
      error: {
        code: "internal_error",
        message: "Traceback (most recent call last): secret-config-value",
        correlation_id: CID,
      },
    }),
    { status: 500, headers: { "Content-Type": "application/json" } },
  );

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("renders the page and shows loading, then the API result", async () => {
    respondWith(ok);
    render(<App />);
    expect(screen.getByRole("heading", { name: "Plan on a Page", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Checking service");
    expect(await screen.findByTestId("status-result")).toHaveTextContent("Service is ok (test).");
  });

  it("shows only a generic message and a reference when the API fails", async () => {
    respondWith(leakyFailure);
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).toHaveTextContent(CID);
    expect(document.body).not.toHaveTextContent("Traceback");
    expect(document.body).not.toHaveTextContent("secret-config-value");
  });

  it("shows a generic message when the network is down", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).not.toHaveTextContent("Reference");
  });

  it("recovers when the user retries", async () => {
    const fetchMock = respondWith(leakyFailure, ok);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("status-result")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
