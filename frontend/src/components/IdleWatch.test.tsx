import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IdleWatch } from "./IdleWatch";
import { csrfRoute, envelope, json, mockApi } from "../test/mockApi";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const session = (remaining: number) => () =>
  json({ created_at: "2026-11-10T00:00:00Z", expires_at: "2026-11-11T00:00:00Z", idle_seconds_remaining: remaining });

function setup(routes: Parameters<typeof mockApi>[0], remaining = 900) {
  const api = mockApi({ "GET /api/csrf": csrfRoute, ...routes });
  const onEnded = vi.fn();
  const view = render(<IdleWatch remaining={remaining} onEnded={onEnded} />);
  return { ...api, onEnded, ...view };
}
const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

describe("IdleWatch", () => {
  it("says nothing while there is plenty of time", async () => {
    vi.useFakeTimers();
    setup({ "GET /api/session": session(900) });
    await tick(5000);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("warns two minutes before the end, with the time left and a button to stay", async () => {
    vi.useFakeTimers();
    const started = Date.now();
    const draining = () => session(900 - Math.round((Date.now() - started) / 1000))(); // the server's own clock
    setup({ "GET /api/session": draining }, 900);
    await tick(780_000 - 1000); // 13 minutes of nothing (the re-reads agree with the clock)
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await tick(2000);
    expect(screen.getByRole("alert")).toHaveTextContent("about to end");
    expect(screen.getByText(/Time left: 1:5\d/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep my session" })).toBeEnabled();
  });

  it("ends the session when the time is up", async () => {
    vi.useFakeTimers();
    const { onEnded } = setup({ "GET /api/session": () => envelope("unauthenticated", 401) }, 3);
    await tick(4000);
    expect(onEnded).toHaveBeenCalledTimes(1); // once, however many ticks follow
    await tick(5000);
    expect(onEnded).toHaveBeenCalledTimes(1);
  });

  it("goes back to the entry page when the server says the session is gone", async () => {
    vi.useFakeTimers();
    const { onEnded } = setup({ "GET /api/session": () => envelope("unauthenticated", 401) }, 600);
    await tick(21_000); // the next re-read
    expect(onEnded).toHaveBeenCalledTimes(1);
  });

  it("keeps the session when the user asks: the clock starts again and the warning goes", async () => {
    const { calls } = setup({ "POST /api/session/keepalive": session(900) }, 60);
    await userEvent.click(await screen.findByRole("button", { name: "Keep my session" }));
    await vi.waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(calls.some((c) => c.key === "POST /api/session/keepalive")).toBe(true);
  });

  it("counts typing and other real use of the page as activity, reported at most once a minute", async () => {
    vi.useFakeTimers();
    const { calls } = setup({ "POST /api/session/keepalive": session(900) }, 900);
    const stays = () => calls.filter((c) => c.key === "POST /api/session/keepalive").length;
    await tick(61_000);
    for (let i = 0; i < 20; i++) fireEvent.keyDown(window, { key: "a" }); // a burst of typing
    await tick(0);
    expect(stays()).toBe(1);
    await tick(30_000);
    fireEvent.keyDown(window, { key: "b" });
    await tick(0);
    expect(stays()).toBe(1); // still within the minute
    await tick(31_000);
    fireEvent.pointerMove(window);
    await tick(0);
    expect(stays()).toBe(2);
  });

  it("takes the warning away when the person starts typing again", async () => {
    vi.useFakeTimers();
    setup({ "POST /api/session/keepalive": session(900), "GET /api/session": () => json({ idle_seconds_remaining: 100 }) }, 100);
    await tick(61_000);
    expect(screen.queryByRole("alert")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "a" });
    await tick(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not count a quiet page, however long it stays open", async () => {
    vi.useFakeTimers();
    const { calls } = setup({ "GET /api/session": () => json({ idle_seconds_remaining: 600 }) }, 600);
    await tick(300_000);
    expect(calls.some((c) => c.key === "POST /api/session/keepalive")).toBe(false);
  });

  it("adopts the server's deadline when another tab was active", async () => {
    vi.useFakeTimers();
    setup({ "GET /api/session": session(900) }, 100); // already warning
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await tick(21_000); // the re-read says there are fifteen minutes left
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hands a lost session to the page when staying fails with a 401", async () => {
    const { onEnded } = setup({ "POST /api/session/keepalive": () => envelope("unauthenticated", 401) }, 60);
    await userEvent.click(await screen.findByRole("button", { name: "Keep my session" }));
    await vi.waitFor(() => expect(onEnded).toHaveBeenCalledTimes(1));
  });
});
