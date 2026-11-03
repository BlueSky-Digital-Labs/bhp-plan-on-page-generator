import { afterEach, describe, expect, it } from "vitest";
import { ROUTES, navigate, readNotice, safeRoute } from "./router";

afterEach(() => window.history.replaceState(null, "", "/"));

describe("safeRoute", () => {
  it("follows plain in-app paths", () => {
    expect(safeRoute("/conversation")).toBe("/conversation");
    expect(safeRoute("/conversation/next-step")).toBe("/conversation/next-step");
  });

  it.each([
    "https://evil.example/x",
    "//evil.example",
    "/\\evil.example",
    "javascript:alert(1)",
    "/a?b=c",
    "/a#frag",
    "/a b",
    "",
    "relative/path",
    null,
    undefined,
    42,
    { next: "/x" },
  ])("never follows %j off-route: falls back to the conversation route", (value) => {
    expect(safeRoute(value)).toBe(ROUTES.conversation);
  });
});

describe("navigate", () => {
  it("pushes or replaces the path and carries only a fixed notice", () => {
    navigate("/start");
    expect(window.location.pathname).toBe("/start");
    expect(readNotice()).toBeNull();
    navigate("/", { replace: true, notice: "access_required" });
    expect(window.location.pathname).toBe("/");
    expect(readNotice()).toBe("access_required");
  });
});
