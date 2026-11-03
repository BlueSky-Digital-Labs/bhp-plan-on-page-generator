import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest } from "./client";

const CID = "0123456789abcdef0123456789abcdef";

function mockFetch(response: Response | Error) {
  const fn = vi.fn(async () => {
    if (response instanceof Error) throw response;
    return response;
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function json(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

async function failure(path = "/api/x"): Promise<ApiError> {
  try {
    await apiRequest(path);
  } catch (error) {
    return error as ApiError;
  }
  throw new Error("expected apiRequest to reject");
}

afterEach(() => vi.unstubAllGlobals());

describe("apiRequest", () => {
  it("returns parsed JSON on success and sends same-origin JSON requests", async () => {
    const fetchMock = mockFetch(json({ status: "ok" }));
    await expect(apiRequest("/api/status")).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/status",
      expect.objectContaining({ method: "GET", credentials: "same-origin" }),
    );
  });

  it("serialises a request body as JSON", async () => {
    const fetchMock = mockFetch(json({}));
    await apiRequest("/api/x", { method: "POST", body: { a: 1 } });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/x",
      expect.objectContaining({ body: '{"a":1}', headers: expect.objectContaining({ "Content-Type": "application/json" }) }),
    );
  });

  it("sends a form body without a JSON content type", async () => {
    const fetchMock = mockFetch(json({ status: "ok" }));
    await apiRequest("/api/access", { method: "POST", form: { access_code: "a b&c" } });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(String(init.body)).toBe("access_code=a+b%26c");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("fetches a CSRF token first and sends it as X-CSRF-Token", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ csrf_token: "tok" }))
      .mockResolvedValueOnce(json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await apiRequest("/api/sessions", { method: "POST", csrf: true });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/csrf");
    expect(fetchMock.mock.calls[1][1].headers["X-CSRF-Token"]).toBe("tok");
  });

  it("does not send the state-changing request if the CSRF token cannot be fetched", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ error: { code: "x", message: "m", correlation_id: CID } }, { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiRequest("/api/sessions", { method: "POST", csrf: true })).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("accepts a 204 when the endpoint is declared as no-content", async () => {
    mockFetch(new Response(null, { status: 204 }));
    await expect(apiRequest("/api/session", { method: "DELETE", noContent: true })).resolves.toBeUndefined();
  });

  it("refuses paths outside /api/", async () => {
    mockFetch(json({}));
    await expect(apiRequest("https://elsewhere.example/api/x")).rejects.toThrow("same-origin");
  });

  it("maps the error envelope to ApiError with code and correlation id", async () => {
    mockFetch(
      json({ error: { code: "internal_error", message: "x", correlation_id: CID } }, { status: 500 }),
    );
    const error = await failure();
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 500, code: "internal_error", correlationId: CID });
  });

  it("never carries the server message into the error", async () => {
    mockFetch(
      json(
        { error: { code: "internal_error", message: "Traceback: secret plan text", correlation_id: CID } },
        { status: 500 },
      ),
    );
    const error = await failure();
    expect(error.message).not.toContain("secret");
    expect(JSON.stringify(error)).not.toContain("secret");
  });

  it("drops a correlation id that is not a server-style reference", async () => {
    mockFetch(
      json({ error: { code: "x", message: "m", correlation_id: "<img src=x onerror=alert(1)>" } }, { status: 500 }),
    );
    expect((await failure()).correlationId).toBeNull();
  });

  it("falls back to the response header when the body is not an envelope", async () => {
    mockFetch(new Response("<html>502 Bad Gateway</html>", { status: 502, headers: { "X-Correlation-ID": CID } }));
    expect(await failure()).toMatchObject({ status: 502, code: "unexpected_response", correlationId: CID });
  });

  it("reports network failures without detail", async () => {
    mockFetch(new TypeError("Failed to fetch: https://internal.example/secret"));
    const error = await failure();
    expect(error).toMatchObject({ status: null, code: "network_error" });
    expect(error.message).not.toContain("internal.example");
  });

  it("treats a non-JSON success body as an invalid response", async () => {
    mockFetch(new Response("not json", { status: 200 }));
    expect(await failure()).toMatchObject({ code: "invalid_response" });
  });
});
