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
    purge_eligible: canvas && appendix,
    lock_reason: canvas && appendix ? null : canvas ? "appendix_not_downloaded" : appendix ? "canvas_not_downloaded" : "both_not_downloaded",
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
  it("tells someone who cannot finish how to save and carry on, and offers the two downloads", async () => {
    setup({});
    expect(await screen.findByRole("heading", { name: "Save your progress" })).toBeInTheDocument();
    expect(screen.getByText(/You do not have to finish now/)).toBeInTheDocument();
    expect(screen.getByText(/attach either file to your first answer/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download canvas so far/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download appendix so far/ })).toBeInTheDocument();
    expect(await screen.findByText(/Stopping is locked/)).toBeInTheDocument();
    expect(stopButton()).not.toBeInTheDocument();
  });

  it.each([
    ["canvas", "PDF", "GET /api/canvas-file?format=pdf&revision=4", "idea-canvas-draft-r4.pdf"],
    ["canvas", "PowerPoint", "GET /api/canvas-file?format=pptx&revision=4", "idea-canvas-draft-r4.pptx"],
    ["appendix", "Word", "GET /api/appendix?format=docx&revision=4", "idea-appendix-draft-r4.docx"],
  ])("downloads the %s as %s for the current revision and re-reads the status", async (menu, item, key, name) => {
    const { calls } = setup({ [key]: file(name) });
    await screen.findByText(/Stopping is locked/);
    await choose(`Download ${menu}`, new RegExp(item));
    await waitFor(() => expect(saved).toEqual([name]));
    expect(calls.some((c) => c.key === key)).toBe(true);
    await waitFor(() => expect(calls.filter((c) => c.key === "GET /api/downloads").length).toBeGreaterThan(1));
  });

  it("offers Stop session only once the server says both are saved, and stops after confirmation", async () => {
    const { calls, onStopped } = setup({
      "GET /api/downloads": status(true, true),
      "POST /api/session/purge": () => new Response(null, { status: 204 }),
    });
    await userEvent.click(await screen.findByRole("button", { name: /^Stop session/ }));
    expect(screen.getByText("Stop this session now?")).toBeInTheDocument();
    expect(screen.getByText(/attach one of the files you downloaded/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Yes, stop and delete everything" }));
    await waitFor(() => expect(onStopped).toHaveBeenCalledOnce());
    expect(JSON.parse(String(calls.find((c) => c.key === "POST /api/session/purge")!.init?.body))).toEqual({ revision: 4 });
  });

  it("lets the user keep going instead of stopping", async () => {
    const { calls, onStopped } = setup({ "GET /api/downloads": status(true, true) });
    await userEvent.click(await screen.findByRole("button", { name: /^Stop session/ }));
    await userEvent.click(screen.getByRole("button", { name: "Keep going" }));
    expect(screen.queryByText("Stop this session now?")).not.toBeInTheDocument();
    expect(onStopped).not.toHaveBeenCalled();
    expect(calls.some((c) => c.key === "POST /api/session/purge")).toBe(false);
  });

  it("says stopping is still locked if the server refuses, and does not leave the page", async () => {
    const { onStopped } = setup({
      "GET /api/downloads": status(true, true),
      "POST /api/session/purge": () => envelope("purge_locked", 409),
    });
    await userEvent.click(await screen.findByRole("button", { name: /^Stop session/ }));
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
    await screen.findByText(/Stopping is locked/);
    await choose("Download canvas", /PDF/);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(text);
    expect(alert).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
    expect(saved).toEqual([]);
  });

  it("hands a lost session to the page", async () => {
    const { onAccessLost } = setup({ "GET /api/canvas-file?format=pdf&revision=4": () => envelope("unauthenticated", 401) });
    await screen.findByText(/Stopping is locked/);
    await choose("Download canvas", /PDF/);
    await waitFor(() => expect(onAccessLost).toHaveBeenCalled());
  });
});
