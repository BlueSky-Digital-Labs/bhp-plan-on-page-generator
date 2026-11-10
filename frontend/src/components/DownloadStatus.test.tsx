import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DownloadStatus } from "./DownloadStatus";
import { csrfRoute, envelope, json, mockApi } from "../test/mockApi";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

type Routes = Parameters<typeof mockApi>[0];
const status = (canvas: boolean, appendix: boolean, extra: Record<string, unknown> = {}) => () =>
  json({
    revision: 3,
    canvas_downloaded: canvas,
    appendix_downloaded: appendix,
    purge_eligible: canvas, // the one-page canvas alone unlocks it; the appendix never holds it back
    lock_reason: canvas ? null : "canvas_not_downloaded",
    ...extra,
  });

function setup(routes: Routes, refreshKey = 0, revision = 3) {
  const api = mockApi({ "GET /api/csrf": csrfRoute, ...routes });
  const handlers = { onOutOfDate: vi.fn(), onPurged: vi.fn(), onAccessLost: vi.fn() };
  const view = render(<DownloadStatus revision={revision} refreshKey={refreshKey} {...handlers} />);
  // What a download attempt does on the page: bump the key, so the status is read again.
  const readAgain = (key: number) =>
    view.rerender(<DownloadStatus revision={revision} refreshKey={key} {...handlers} />);
  return { ...api, ...handlers, ...view, readAgain };
}

const reads = (calls: Array<{ key: string }>) => calls.filter((c) => c.key === "GET /api/downloads").length;
const deleteButton = () => screen.queryByRole("button", { name: /^Delete my session/ });
// The button is always there; it unlocks when the server says the one-page canvas was sent.
const enabledDelete = async () => {
  await waitFor(() => expect(deleteButton()).toBeEnabled());
  return deleteButton()!;
};
// Waits until the status has been read and shown.
const settled = async (calls: Array<{ key: string }>) => {
  await waitFor(() => expect(reads(calls)).toBeGreaterThan(0));
  await act(async () => {});
};

describe("DownloadStatus: the state matrix", () => {
  it("shows the canvas as not downloaded and keeps deleting locked, with no long explanation", async () => {
    const { calls } = setup({ "GET /api/downloads": status(false, false) });
    await settled(calls);
    expect(deleteButton()).toBeDisabled();
    expect(deleteButton()).toHaveAccessibleDescription("Download the one-page canvas, or submit it by email, first.");
    expect(screen.queryByText(/Deleting is locked|Still to download|Save the documents/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Refresh status" })).not.toBeInTheDocument();
  });

  it("offers deleting as soon as the canvas is downloaded, without the appendix", async () => {
    setup({ "GET /api/downloads": status(true, false) });
    await enabledDelete();
  });

  it("does not unlock on the appendix alone", async () => {
    const { calls } = setup({ "GET /api/downloads": status(false, true) });
    await settled(calls);
    expect(deleteButton()).toBeDisabled();
  });

  it.each(["not_confirmed", "not_ready", "turn_in_progress"])("keeps deleting locked for a %s lock", async (reason) => {
    const { calls } = setup({ "GET /api/downloads": status(false, false, { lock_reason: reason }) });
    await settled(calls);
    expect(deleteButton()).toBeDisabled();
  });
});

describe("DownloadStatus: refreshing", () => {
  it("re-reads when the page bumps the key after a download, and only reads: nothing is downloaded or sent", async () => {
    vi.useFakeTimers();
    const { calls, readAgain } = setup({ "GET /api/downloads": [status(false, false), status(true, false)] });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(deleteButton()).toBeDisabled();
    readAgain(1);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(deleteButton()).toBeEnabled();
    expect(calls.map((c) => c.key).filter((k) => k !== "GET /api/csrf")).toEqual(["GET /api/downloads", "GET /api/downloads"]);
  });

  it("follows each download attempt with three more reads, then stops", async () => {
    vi.useFakeTimers();
    const { calls } = setup({ "GET /api/downloads": status(false, false) }, 1);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(reads(calls)).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(reads(calls)).toBe(2);
    await act(() => vi.advanceTimersByTimeAsync(2500));
    expect(reads(calls)).toBe(3);
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(reads(calls)).toBe(4);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(reads(calls)).toBe(4); // bounded: no polling after that
  });

  it("catches a download that finished just after the first read", async () => {
    vi.useFakeTimers();
    setup({ "GET /api/downloads": [status(false, false), status(true, false)] }, 1);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(deleteButton()).toBeDisabled();
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(deleteButton()).toBeEnabled();
  });

  it("starts a new set of follow-ups for the next download attempt", async () => {
    const api = mockApi({ "GET /api/csrf": csrfRoute, "GET /api/downloads": status(false, false) });
    vi.useFakeTimers();
    const handlers = { onOutOfDate: vi.fn(), onPurged: vi.fn(), onAccessLost: vi.fn() };
    const view = render(<DownloadStatus revision={3} refreshKey={1} {...handlers} />);
    await act(() => vi.advanceTimersByTimeAsync(5000));
    view.rerender(<DownloadStatus revision={3} refreshKey={2} {...handlers} />);
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(reads(api.calls)).toBe(6);
  });

  it("stops reading once it is no longer on screen", async () => {
    vi.useFakeTimers();
    const { calls, unmount } = setup({ "GET /api/downloads": status(false, false) }, 1);
    await act(() => vi.advanceTimersByTimeAsync(0));
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(reads(calls)).toBe(1);
  });

  it("lets only the newest read decide what is shown", async () => {
    vi.useFakeTimers();
    let first!: () => void;
    const slowEligible = () => new Promise<Response>((resolve) => (first = () => resolve(status(true, true)())));
    setup({ "GET /api/downloads": [slowEligible, status(false, false)] }, 1);
    await act(() => vi.advanceTimersByTimeAsync(1500)); // the follow-up read answers while the first is still out
    expect(deleteButton()).toBeDisabled();
    await act(async () => first()); // the older, slower answer arrives last
    expect(deleteButton()).toBeDisabled();
  });
});

describe("DownloadStatus: never showing stale eligibility", () => {
  it("locks the delete action while the status is being read again", async () => {
    let release!: () => void;
    const { readAgain } = setup({
      "GET /api/downloads": [status(true, true), () => new Promise<Response>((resolve) => (release = () => resolve(status(true, true)())))],
    });
    await enabledDelete();
    readAgain(1);
    await waitFor(() => expect(deleteButton()).toBeDisabled()); // not offered on the strength of an old answer
    release();
    await enabledDelete();
  });

  it("drops the confirmation and locks the action if a later read fails", async () => {
    const { readAgain } = setup({ "GET /api/downloads": [status(true, true), () => envelope("service_unavailable", 503)] });
    await userEvent.click(await enabledDelete());
    expect(screen.getByRole("button", { name: "Yes, delete everything" })).toBeInTheDocument();
    readAgain(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not check the download status");
    expect(deleteButton()).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Yes, delete everything" })).not.toBeInTheDocument();
  });

  it("starts with a failed read as a locked state that can be checked again", async () => {
    setup({ "GET /api/downloads": [() => envelope("service_unavailable", 503), status(true, false)] });
    expect(await screen.findByRole("alert")).toHaveTextContent("Deleting stays locked until it has been checked");
    expect(deleteButton()).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await enabledDelete();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("treats a lost connection as a failed read", async () => {
    setup({
      "GET /api/downloads": () => {
        throw new TypeError("network down");
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not check the download status");
    expect(deleteButton()).toBeDisabled();
  });

  it("resets when the next read says the canvas is no longer downloaded (an amendment)", async () => {
    const { readAgain } = setup({ "GET /api/downloads": [status(true, true), status(false, false)] });
    await enabledDelete();
    readAgain(1);
    await waitFor(() => expect(deleteButton()).toBeDisabled());
  });

  it("hands over to the page when the status is for a different revision, and shows no eligibility", async () => {
    const { onOutOfDate } = setup({ "GET /api/downloads": status(true, true, { revision: 4 }) });
    await waitFor(() => expect(onOutOfDate).toHaveBeenCalledTimes(1));
    expect(deleteButton()).toBeDisabled();
  });

  it("returns to entry when the session has ended, and does not keep reading", async () => {
    vi.useFakeTimers();
    const { onAccessLost, calls } = setup({ "GET /api/downloads": () => envelope("unauthenticated", 401) }, 1);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(onAccessLost).toHaveBeenCalled();
    expect(deleteButton()).toBeDisabled();
    const before = reads(calls);
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(reads(calls)).toBeLessThanOrEqual(before + 3); // only the bounded follow-ups, each ends the same way
    expect(onAccessLost.mock.calls.length).toBeGreaterThanOrEqual(1);
  });
});

describe("DownloadStatus: deleting", () => {
  const eligible = { "GET /api/downloads": status(true, true) };
  const sentPurges = (calls: Array<{ key: string; init?: RequestInit }>) =>
    calls.filter((c) => c.key === "POST /api/session/purge").map((c) => JSON.parse(String(c.init?.body)));

  it("asks for confirmation first and sends nothing until the user says yes", async () => {
    const { calls } = setup({ ...eligible, "POST /api/session/purge": () => new Response(null, { status: 204 }) });
    await userEvent.click(await enabledDelete());
    expect(screen.getByText(/cannot be recovered/)).toBeInTheDocument();
    expect(sentPurges(calls)).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Keep my session" }));
    expect(screen.queryByRole("button", { name: "Yes, delete everything" })).not.toBeInTheDocument();
    expect(sentPurges(calls)).toHaveLength(0);
  });

  it("asks in a window over the page, focused on the safe choice, and Escape closes it without sending", async () => {
    const { calls } = setup({ ...eligible, "POST /api/session/purge": () => new Response(null, { status: 204 }) });
    const opener = await enabledDelete();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); // nothing is shown in the page itself
    await userEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Delete this session now?" });
    expect(dialog).toHaveAccessibleDescription(/cannot be recovered/);
    expect(within(dialog).getByRole("button", { name: "Keep my session" })).toHaveFocus();
    fireEvent(dialog, new Event("cancel", { cancelable: true })); // what the browser sends for Escape
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sentPurges(calls)).toHaveLength(0);
  });

  it("sends only the revision shown, then leaves the page", async () => {
    const { calls, onPurged } = setup({ ...eligible, "POST /api/session/purge": () => new Response(null, { status: 204 }) });
    await userEvent.click(await enabledDelete());
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    await waitFor(() => expect(onPurged).toHaveBeenCalledTimes(1));
    expect(sentPurges(calls)).toEqual([{ revision: 3 }]); // no eligibility or download flags from the browser
  });

  it("only sends once while the deletion is running", async () => {
    let release!: () => void;
    const { calls } = setup({
      ...eligible,
      "POST /api/session/purge": () => new Promise<Response>((resolve) => (release = () => resolve(new Response(null, { status: 204 })))),
    });
    await userEvent.click(await enabledDelete());
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    expect(screen.getByRole("button", { name: "Deleting…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Keep my session" })).toBeDisabled();
    expect(sentPurges(calls)).toHaveLength(1);
    release();
  });

  it("when the server refuses (it is still locked), says so, changes nothing and reads the status again", async () => {
    const { onPurged, calls } = setup({
      "GET /api/downloads": [status(true, true), status(false, false)],
      "POST /api/session/purge": () => envelope("purge_locked", 409),
    });
    await userEvent.click(await enabledDelete());
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Deleting is still locked. Please download the one-page canvas or submit it by email first.");
    expect(onPurged).not.toHaveBeenCalled();
    await waitFor(() => expect(deleteButton()).toBeDisabled());
    expect(reads(calls)).toBe(2);
  });

  it("when the canvas changed under it, says so and hands over to the page", async () => {
    const { onOutOfDate, onPurged } = setup({ ...eligible, "POST /api/session/purge": () => envelope("revision_conflict", 409) });
    await userEvent.click(await enabledDelete());
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The canvas changed since it was shown");
    expect(onOutOfDate).toHaveBeenCalled();
    expect(onPurged).not.toHaveBeenCalled();
  });

  it("returns to entry when the session ended during the deletion", async () => {
    const { onAccessLost, onPurged } = setup({ ...eligible, "POST /api/session/purge": () => envelope("unauthenticated", 401) });
    await userEvent.click(await enabledDelete());
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    await waitFor(() => expect(onAccessLost).toHaveBeenCalled());
    expect(onPurged).not.toHaveBeenCalled();
  });

  it("gives fixed guidance for any other failure and never shows server text", async () => {
    setup({ ...eligible, "POST /api/session/purge": () => envelope("something_new", 500) });
    await userEvent.click(await enabledDelete());
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("That could not be done. Please try again.");
    expect(alert).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
  });
});
