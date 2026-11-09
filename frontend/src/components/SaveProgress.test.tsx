import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SaveProgress } from "./SaveProgress";
import { csrfRoute, envelope, json, mockApi } from "../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

type Routes = Parameters<typeof mockApi>[0];
const status = (canvas: boolean, appendix: boolean) => () =>
  json({
    revision: 4,
    canvas_downloaded: canvas,
    appendix_downloaded: appendix,
    purge_eligible: canvas, // the one-page canvas alone unlocks stopping
    lock_reason: canvas ? null : "canvas_not_downloaded",
    complete: false,
  });
const file = (name: string) => () =>
  new Response(new Blob(["bytes"]), { status: 200, headers: { "Content-Disposition": `attachment; filename="${name}"` } });

let saved: string[];
beforeEach(() => {
  saved = [];
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:test", revokeObjectURL: () => {} }));
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download);
  });
});

function setup(routes: Routes) {
  const api = mockApi({ "GET /api/csrf": csrfRoute, "GET /api/downloads": status(false, false), ...routes });
  const handlers = { onStopped: vi.fn(), onAccessLost: vi.fn(), onOutOfDate: vi.fn() };
  render(<SaveProgress revision={4} {...handlers} />);
  return { ...api, ...handlers };
}

const stopButton = () => screen.queryByRole("button", { name: /^Stop session/ });
const choose = async (menu: string, item: string | RegExp) => {
  await userEvent.click(screen.getByRole("button", { name: new RegExp(menu) }));
  await userEvent.click(await screen.findByRole("menuitem", { name: item }));
};

describe("SaveProgress", () => {
  it("is just the two download buttons and Stop session, with nothing else on it", async () => {
    setup({});
    expect(await screen.findByRole("heading", { name: "Save your progress" })).toHaveClass("sr-only"); // named, not shown
    expect(screen.getByRole("button", { name: /Download draft canvas/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download draft appendix/ })).toBeInTheDocument();
    expect(stopButton()).toBeInTheDocument();
    expect(screen.queryByText(/You do not have to finish now/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Downloaded|locked|Refresh status/)).not.toBeInTheDocument();
    expect(document.querySelector("details, ul")).toBeNull();
  });

  it("keeps Stop session locked until the one-page canvas is downloaded, with the reason for assistive technology", async () => {
    setup({});
    await waitFor(() => expect(stopButton()).toBeDisabled());
    expect(stopButton()).toHaveAccessibleDescription("Download the one-page canvas first.");
  });

  it("unlocks Stop session the moment the canvas is downloaded, even without the appendix", async () => {
    setup({ "GET /api/downloads": status(true, false) });
    await waitFor(() => expect(stopButton()).toBeEnabled());
  });

  it.each([
    ["canvas", "PDF", "GET /api/canvas-file?format=pdf&revision=4", "idea-canvas-draft-r4.pdf"],
    ["canvas", "PowerPoint", "GET /api/canvas-file?format=pptx&revision=4", "idea-canvas-draft-r4.pptx"],
    ["appendix", "Word", "GET /api/appendix?format=docx&revision=4", "idea-appendix-draft-r4.docx"],
  ])("downloads the %s as %s for the current revision and re-reads the status", async (menu, item, key, name) => {
    const { calls } = setup({ [key]: file(name) });
    await screen.findByRole("button", { name: /^Stop session/ });
    await choose(`Download draft ${menu}`, new RegExp(item));
    await waitFor(() => expect(saved).toEqual([name]));
    expect(calls.some((c) => c.key === key)).toBe(true);
    await waitFor(() => expect(calls.filter((c) => c.key === "GET /api/downloads").length).toBeGreaterThan(1));
  });

  it("stops after the user confirms in the window that opens", async () => {
    const { calls, onStopped } = setup({
      "GET /api/downloads": status(true, true),
      "POST /api/session/purge": () => new Response(null, { status: 204 }),
    });
    await waitFor(() => expect(stopButton()).toBeEnabled());
    await userEvent.click(stopButton()!);
    const dialog = screen.getByRole("dialog", { name: "Stop this session now?" }); // a window, not part of the card
    expect(dialog).toHaveTextContent(/attach one of the files you downloaded/);
    await userEvent.click(screen.getByRole("button", { name: "Yes, stop and delete everything" }));
    await waitFor(() => expect(onStopped).toHaveBeenCalledOnce());
    expect(JSON.parse(String(calls.find((c) => c.key === "POST /api/session/purge")!.init?.body))).toEqual({ revision: 4 });
  });

  it("lets the user keep going instead of stopping", async () => {
    const { calls, onStopped } = setup({ "GET /api/downloads": status(true, true) });
    await waitFor(() => expect(stopButton()).toBeEnabled());
    await userEvent.click(stopButton()!);
    await userEvent.click(screen.getByRole("button", { name: "Keep going" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onStopped).not.toHaveBeenCalled();
    expect(calls.some((c) => c.key === "POST /api/session/purge")).toBe(false);
  });

  it("says stopping is still locked if the server refuses, and does not leave the page", async () => {
    const { onStopped } = setup({
      "GET /api/downloads": status(true, true),
      "POST /api/session/purge": () => envelope("purge_locked", 409),
    });
    await waitFor(() => expect(stopButton()).toBeEnabled());
    await userEvent.click(stopButton()!);
    await userEvent.click(screen.getByRole("button", { name: "Yes, stop and delete everything" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Stopping is still locked");
    expect(onStopped).not.toHaveBeenCalled();
  });

  it.each([
    ["nothing_to_download", /Answer at least one question first/],
    ["turn_in_progress", /still replying/],
    ["something_new", /That could not be done/],
  ])("shows fixed guidance when a download is refused (%s), never the server's words", async (code, text) => {
    setup({ "GET /api/canvas-file?format=pdf&revision=4": () => envelope(code, 409) });
    await screen.findByRole("button", { name: /^Stop session/ });
    await choose("Download draft canvas", /PDF/);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(text);
    expect(alert).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
    expect(saved).toEqual([]);
  });

  it("hands a lost session to the page", async () => {
    const { onAccessLost } = setup({ "GET /api/canvas-file?format=pdf&revision=4": () => envelope("unauthenticated", 401) });
    await screen.findByRole("button", { name: /^Stop session/ });
    await choose("Download draft canvas", /PDF/);
    await waitFor(() => expect(onAccessLost).toHaveBeenCalled());
  });
});
