import { act, render, screen, waitFor, within } from "@testing-library/react";
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
    purge_eligible: canvas && appendix,
    lock_reason: canvas && appendix ? null : canvas ? "appendix_not_downloaded" : appendix ? "canvas_not_downloaded" : "both_not_downloaded",
    ...extra,
  });

function setup(routes: Routes, refreshKey = 0, revision = 3) {
  const api = mockApi({ "GET /api/csrf": csrfRoute, ...routes });
  const handlers = { onOutOfDate: vi.fn(), onPurged: vi.fn(), onAccessLost: vi.fn() };
  const view = render(<DownloadStatus revision={revision} refreshKey={refreshKey} {...handlers} />);
  return { ...api, ...handlers, ...view };
}

const reads = (calls: Array<{ key: string }>) => calls.filter((c) => c.key === "GET /api/downloads").length;
const deleteButton = () => screen.queryByRole("button", { name: /^Delete my session/ });
const rowText = (name: string) => screen.getByText(name).closest("li")!;

describe("DownloadStatus: the state matrix", () => {
  it("shows both documents as not downloaded, says deleting is locked and names both", async () => {
    setup({ "GET /api/downloads": status(false, false) });
    expect(await screen.findByText(/Deleting is locked/)).toBeInTheDocument();
    expect(rowText("One-page canvas")).toHaveTextContent("Not downloaded yet");
    expect(rowText("Appendix")).toHaveTextContent("Not downloaded yet");
    expect(screen.getByText(/Still to download: the one-page canvas and the appendix\./)).toBeInTheDocument();
    expect(deleteButton()).not.toBeInTheDocument();
  });

  it("shows the canvas as downloaded and names only the appendix as outstanding", async () => {
    setup({ "GET /api/downloads": status(true, false) });
    await screen.findByText(/Deleting is locked/);
    expect(rowText("One-page canvas")).toHaveTextContent("Downloaded");
    expect(rowText("Appendix")).toHaveTextContent("Not downloaded yet");
    expect(screen.getByText("Still to download: the appendix.")).toBeInTheDocument();
    expect(deleteButton()).not.toBeInTheDocument();
  });

  it("shows the appendix as downloaded and names only the canvas as outstanding", async () => {
    setup({ "GET /api/downloads": status(false, true) });
    await screen.findByText(/Deleting is locked/);
    expect(rowText("One-page canvas")).toHaveTextContent("Not downloaded yet");
    expect(rowText("Appendix")).toHaveTextContent("Downloaded");
    expect(screen.getByText("Still to download: the one-page canvas.")).toBeInTheDocument();
    expect(deleteButton()).not.toBeInTheDocument();
  });

  it("offers deleting only when the server says both are downloaded", async () => {
    setup({ "GET /api/downloads": status(true, true) });
    expect(await screen.findByRole("status")).toHaveTextContent("Both documents have been downloaded");
    expect(rowText("One-page canvas")).toHaveTextContent("Downloaded");
    expect(rowText("Appendix")).toHaveTextContent("Downloaded");
    expect(screen.queryByText(/Deleting is locked/)).not.toBeInTheDocument();
    expect(deleteButton()).toBeInTheDocument();
  });

  it.each(["not_confirmed", "not_ready", "turn_in_progress"])("explains a %s lock without naming a document", async (reason) => {
    setup({ "GET /api/downloads": status(false, false, { lock_reason: reason }) });
    expect(await screen.findByText("The current canvas must be confirmed and both documents downloaded first.")).toBeInTheDocument();
    expect(screen.queryByText(/Still to download/)).not.toBeInTheDocument();
  });

  it("says in words, not only by colour or symbol, what each state is", async () => {
    setup({ "GET /api/downloads": status(true, false) });
    await screen.findByText(/Deleting is locked/);
    const list = within(screen.getByRole("list"));
    expect(list.getByText(/Downloaded/)).toBeInTheDocument();
    expect(list.getByText(/Not downloaded yet/)).toBeInTheDocument();
  });

  it("reminds the user to save both documents and does not claim the download proves saving or submission", async () => {
    setup({ "GET /api/downloads": status(false, false) });
    const help = await screen.findByText(/Save both documents somewhere you can find them/);
    expect(help).toHaveTextContent("It cannot check that the file was saved on your device");
    expect(help).toHaveTextContent("nothing is submitted to a backlog");
    expect(help).toHaveTextContent("does not download the file again");
    expect(document.body).not.toHaveTextContent(/\b(saved to your|submitted to the)\b/i);
  });
});

describe("DownloadStatus: refreshing", () => {
  it("re-reads on the manual button, and only reads: nothing is downloaded or sent", async () => {
    const { calls } = setup({ "GET /api/downloads": [status(false, false), status(true, false)] });
    await screen.findByText(/Deleting is locked/);
    await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await waitFor(() => expect(rowText("One-page canvas")).toHaveTextContent("Downloaded"));
    expect(calls.map((c) => c.key).filter((k) => k !== "GET /api/csrf")).toEqual(["GET /api/downloads", "GET /api/downloads"]);
  });

  it("follows each download attempt with two more reads, then stops", async () => {
    vi.useFakeTimers();
    const { calls } = setup({ "GET /api/downloads": status(false, false) }, 1);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(reads(calls)).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(reads(calls)).toBe(2);
    await act(() => vi.advanceTimersByTimeAsync(2500));
    expect(reads(calls)).toBe(3);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(reads(calls)).toBe(3); // bounded: no polling after that
  });

  it("catches a download that finished just after the first read", async () => {
    vi.useFakeTimers();
    setup({ "GET /api/downloads": [status(false, false), status(true, false), status(true, true)] }, 1);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(rowText("One-page canvas")).toHaveTextContent("Not downloaded yet");
    await act(() => vi.advanceTimersByTimeAsync(1500));
    expect(rowText("One-page canvas")).toHaveTextContent("Downloaded");
    await act(() => vi.advanceTimersByTimeAsync(2500));
    expect(deleteButton()).toBeInTheDocument();
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
    expect(screen.getByText(/Deleting is locked/)).toBeInTheDocument();
    await act(async () => first()); // the older, slower answer arrives last
    expect(screen.getByText(/Deleting is locked/)).toBeInTheDocument();
    expect(deleteButton()).not.toBeInTheDocument();
  });
});

describe("DownloadStatus: never showing stale eligibility", () => {
  it("hides the delete action while the status is being read again", async () => {
    let release!: () => void;
    setup({
      "GET /api/downloads": [status(true, true), () => new Promise<Response>((resolve) => (release = () => resolve(status(true, true)())))],
    });
    await screen.findByRole("button", { name: /^Delete my session/ });
    await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(deleteButton()).not.toBeInTheDocument(); // not offered on the strength of an old answer
    release();
    expect(await screen.findByRole("button", { name: /^Delete my session/ })).toBeInTheDocument();
  });

  it("drops the delete action and the confirmation if a later read fails", async () => {
    setup({ "GET /api/downloads": [status(true, true), () => envelope("service_unavailable", 503)] });
    await userEvent.click(await screen.findByRole("button", { name: /^Delete my session/ }));
    expect(screen.getByRole("button", { name: "Yes, delete everything" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not check the download status");
    expect(deleteButton()).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Yes, delete everything" })).not.toBeInTheDocument();
    expect(rowText("One-page canvas")).toHaveTextContent("Could not be checked");
  });

  it("starts with a failed read as a locked, retryable state", async () => {
    setup({ "GET /api/downloads": [() => envelope("service_unavailable", 503), status(true, false)] });
    expect(await screen.findByRole("alert")).toHaveTextContent("Deleting stays locked until it has been checked");
    expect(deleteButton()).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await screen.findByText("Still to download: the appendix.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("treats a lost connection as a failed read", async () => {
    setup({
      "GET /api/downloads": () => {
        throw new TypeError("network down");
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not check the download status");
    expect(deleteButton()).not.toBeInTheDocument();
  });

  it("resets when the next read says the documents are no longer downloaded (an amendment)", async () => {
    setup({ "GET /api/downloads": [status(true, true), status(false, false)] });
    await screen.findByRole("button", { name: /^Delete my session/ });
    await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await screen.findByText(/Deleting is locked/);
    expect(deleteButton()).not.toBeInTheDocument();
    expect(rowText("Appendix")).toHaveTextContent("Not downloaded yet");
  });

  it("hands over to the page when the status is for a different revision, and shows no eligibility", async () => {
    const { onOutOfDate } = setup({ "GET /api/downloads": status(true, true, { revision: 4 }) });
    await waitFor(() => expect(onOutOfDate).toHaveBeenCalledTimes(1));
    expect(deleteButton()).not.toBeInTheDocument();
  });

  it("returns to entry when the session has ended, and does not keep reading", async () => {
    vi.useFakeTimers();
    const { onAccessLost, calls } = setup({ "GET /api/downloads": () => envelope("unauthenticated", 401) }, 1);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(onAccessLost).toHaveBeenCalled();
    expect(deleteButton()).not.toBeInTheDocument();
    const before = reads(calls);
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(reads(calls)).toBeLessThanOrEqual(before + 2); // only the bounded follow-ups, each ends the same way
    expect(onAccessLost.mock.calls.length).toBeGreaterThanOrEqual(1);
  });
});

describe("DownloadStatus: deleting", () => {
  const eligible = { "GET /api/downloads": status(true, true) };
  const sentPurges = (calls: Array<{ key: string; init?: RequestInit }>) =>
    calls.filter((c) => c.key === "POST /api/session/purge").map((c) => JSON.parse(String(c.init?.body)));

  it("asks for confirmation first and sends nothing until the user says yes", async () => {
    const { calls } = setup({ ...eligible, "POST /api/session/purge": () => new Response(null, { status: 204 }) });
    await userEvent.click(await screen.findByRole("button", { name: /^Delete my session/ }));
    expect(screen.getByText(/cannot be recovered/)).toBeInTheDocument();
    expect(sentPurges(calls)).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Keep my session" }));
    expect(screen.queryByRole("button", { name: "Yes, delete everything" })).not.toBeInTheDocument();
    expect(sentPurges(calls)).toHaveLength(0);
  });

  it("sends only the revision shown, then leaves the page", async () => {
    const { calls, onPurged } = setup({ ...eligible, "POST /api/session/purge": () => new Response(null, { status: 204 }) });
    await userEvent.click(await screen.findByRole("button", { name: /^Delete my session/ }));
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
    await userEvent.click(await screen.findByRole("button", { name: /^Delete my session/ }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    expect(screen.getByRole("button", { name: "Deleting…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Keep my session" })).toBeDisabled();
    expect(sentPurges(calls)).toHaveLength(1);
    release();
  });

  it("when the server refuses (it is still locked), says so, changes nothing and reads the status again", async () => {
    const { onPurged, calls } = setup({
      "GET /api/downloads": [status(true, true), status(true, false)],
      "POST /api/session/purge": () => envelope("purge_locked", 409),
    });
    await userEvent.click(await screen.findByRole("button", { name: /^Delete my session/ }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Deleting is still locked. Please download both documents first.");
    expect(onPurged).not.toHaveBeenCalled();
    await screen.findByText("Still to download: the appendix.");
    expect(deleteButton()).not.toBeInTheDocument();
    expect(reads(calls)).toBe(2);
  });

  it("when the canvas changed under it, says so and hands over to the page", async () => {
    const { onOutOfDate, onPurged } = setup({ ...eligible, "POST /api/session/purge": () => envelope("revision_conflict", 409) });
    await userEvent.click(await screen.findByRole("button", { name: /^Delete my session/ }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The canvas changed since it was shown");
    expect(onOutOfDate).toHaveBeenCalled();
    expect(onPurged).not.toHaveBeenCalled();
  });

  it("returns to entry when the session ended during the deletion", async () => {
    const { onAccessLost, onPurged } = setup({ ...eligible, "POST /api/session/purge": () => envelope("unauthenticated", 401) });
    await userEvent.click(await screen.findByRole("button", { name: /^Delete my session/ }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    await waitFor(() => expect(onAccessLost).toHaveBeenCalled());
    expect(onPurged).not.toHaveBeenCalled();
  });

  it("gives fixed guidance for any other failure and never shows server text", async () => {
    setup({ ...eligible, "POST /api/session/purge": () => envelope("something_new", 500) });
    await userEvent.click(await screen.findByRole("button", { name: /^Delete my session/ }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, delete everything" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("That could not be done. Please try again.");
    expect(alert).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
  });
});
