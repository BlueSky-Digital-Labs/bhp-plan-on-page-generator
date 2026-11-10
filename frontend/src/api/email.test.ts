import { afterEach, describe, expect, it, vi } from "vitest";
import { submitCanvasEmail } from "./email";
import { csrfRoute, json, mockApi } from "../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("submitCanvasEmail", () => {
  it("posts only the revision, with the forgery token: no address and no text can be sent", async () => {
    const { calls } = mockApi({ "GET /api/csrf": csrfRoute, "POST /api/email": () => json({ sent: 1 }) });
    await expect(submitCanvasEmail(3)).resolves.toEqual({ sent: 1 });
    const post = calls.find((c) => c.key === "POST /api/email")!;
    expect(JSON.parse(String(post.init?.body))).toEqual({ revision: 3 });
    expect(new Headers(post.init?.headers).get("X-CSRF-Token")).toBeTruthy();
  });
});
