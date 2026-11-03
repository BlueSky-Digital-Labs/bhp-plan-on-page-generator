import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SESSION, csrfRoute, envelope, json, mockApi } from "../test/mockApi";
import { StartScreen } from "./StartScreen";

afterEach(() => vi.unstubAllGlobals());

const STARTED = () => json({ ...SESSION, next: "/conversation" }, 201);

function setup(routes: Parameters<typeof mockApi>[0]) {
  const api = mockApi({ "GET /api/csrf": csrfRoute, ...routes });
  const onStarted = vi.fn();
  const onAccessLost = vi.fn();
  render(<StartScreen onStarted={onStarted} onAccessLost={onAccessLost} />);
  return { ...api, onStarted, onAccessLost };
}

describe("StartScreen content", () => {
  it("explains the journey, saving, and the data lifecycle", () => {
    setup({});
    const text = document.body.textContent ?? "";
    expect(screen.getByRole("heading", { name: "Start a new canvas", level: 1 })).toBeInTheDocument();
    for (const step of ["Describe your idea", "Answer a few questions", "Choose your own priority", "Review the canvas"]) {
      expect(text).toContain(step);
    }
    expect(text).toMatch(/save both the one-page canvas and the appendix/i);
    expect(text).toMatch(/you save the canvas and the appendix yourself/i);
    expect(text).toMatch(/not submit anything to a backlog/i);
    expect(text).toMatch(/Australian-hosted/);
    expect(text).toMatch(/purge your session/i);
    expect(text).toMatch(/24 hours/);
    expect(text).toMatch(/even if you close the browser/i);
    expect(text).toMatch(/cannot be recovered/i);
  });

  it("does not claim approval, and does not claim a download proves the file is saved", () => {
    setup({});
    const text = document.body.textContent ?? "";
    expect(text).toMatch(/not a statement that data-handling approval has been granted/i);
    expect(text).toMatch(/cannot confirm that a download has been saved/i);
    expect(text).not.toMatch(/approved|guarantee|successfully saved/i);
  });

  it("offers exactly one start action", () => {
    setup({});
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Start a new canvas" })).toBeEnabled();
  });
});

describe("StartScreen action", () => {
  it("creates a session with a CSRF token and no client-chosen identity", async () => {
    const { calls, onStarted } = setup({ "POST /api/sessions": STARTED });
    await userEvent.click(screen.getByRole("button", { name: "Start a new canvas" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("/conversation"));
    const post = calls.find((c) => c.key === "POST /api/sessions")!;
    expect((post.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("test-csrf-token");
    expect(post.init?.body).toBeUndefined(); // the server decides everything about the session
  });

  it("is operable from the keyboard", async () => {
    const { onStarted } = setup({ "POST /api/sessions": STARTED });
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Start a new canvas" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
  });

  it("ignores duplicate clicks while a request is pending", async () => {
    let release!: (r: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = setup({ "POST /api/sessions": () => pending });
    const button = screen.getByRole("button", { name: "Start a new canvas" });
    await userEvent.click(button);
    expect(screen.getByRole("button", { name: "Starting…" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Starting…" }));
    release(STARTED());
    await waitFor(() => expect(calls.filter((c) => c.key === "POST /api/sessions")).toHaveLength(1));
  });

  it("restores the action and offers a retry after a service failure, without showing success", async () => {
    const { onStarted, calls } = setup({
      "POST /api/sessions": [() => envelope("service_unavailable", 503, "abcdefabcdefabcdefabcdefabcdefab"), STARTED],
    });
    await userEvent.click(screen.getByRole("button", { name: "Start a new canvas" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).toHaveTextContent("abcdefabcdefabcdefabcdefabcdefab");
    expect(document.body).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
    expect(onStarted).not.toHaveBeenCalled();

    const retry = screen.getByRole("button", { name: "Start a new canvas" });
    expect(retry).toBeEnabled();
    await userEvent.click(retry);
    await waitFor(() => expect(onStarted).toHaveBeenCalledTimes(1));
    expect(calls.filter((c) => c.key === "POST /api/sessions")).toHaveLength(2);
  });

  it("returns to entry when access has expired (401) instead of showing a broken action", async () => {
    const { onAccessLost, onStarted } = setup({ "POST /api/sessions": () => envelope("unauthenticated", 401) });
    await userEvent.click(screen.getByRole("button", { name: "Start a new canvas" }));
    await waitFor(() => expect(onAccessLost).toHaveBeenCalled());
    expect(onStarted).not.toHaveBeenCalled();
  });
});
