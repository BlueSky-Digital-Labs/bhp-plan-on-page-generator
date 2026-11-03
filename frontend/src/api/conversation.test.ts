import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiStream } from "./client";
import { streamReply } from "./conversation";
import { envelope, ndjson } from "../test/mockApi";

const ID = "a".repeat(32);

afterEach(() => vi.unstubAllGlobals());

function fetchReturning(response: Response) {
  const fn = vi.fn(async () => response);
  vi.stubGlobal("fetch", fn);
  return fn;
}

async function rejection(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    return error as ApiError;
  }
  throw new Error("expected a rejection");
}

describe("streamReply", () => {
  it("refuses an id that is not a server-style turn id, without making any request", () => {
    const fn = vi.fn();
    vi.stubGlobal("fetch", fn);
    for (const bad of ["../session", "a".repeat(31), "A".repeat(32), `${ID}/../x`, `${ID}?x=1`, ""]) {
      expect(() => streamReply(bad, vi.fn())).toThrow("invalid turn id");
    }
    expect(fn).not.toHaveBeenCalled();
  });

  it("calls the same-origin stream path with cookies, and only passes along known events", async () => {
    const fn = fetchReturning(
      ndjson([
        { type: "delta", text: "Hi" },
        { type: "surprise", text: "<script>" }, // unknown: ignored, never rendered
        { type: "delta", text: 42 }, // wrong shape: ignored
        { type: "failed", reason: "unavailable" },
        { type: "complete" },
      ]),
    );
    const seen: unknown[] = [];
    await streamReply(ID, (e) => seen.push(e));
    expect(fn).toHaveBeenCalledWith(
      `/api/conversation/turns/${ID}/stream`,
      expect.objectContaining({ credentials: "same-origin" }),
    );
    expect(seen).toEqual([
      { type: "delta", text: "Hi" },
      { type: "failed", reason: "unavailable" },
      { type: "complete" },
    ]);
  });
});

describe("apiStream", () => {
  it("refuses paths outside /api/", async () => {
    fetchReturning(ndjson([]));
    await expect(apiStream("https://elsewhere.example/api/x", vi.fn())).rejects.toThrow("same-origin");
  });

  it("reassembles events split across network chunks", async () => {
    const enc = new TextEncoder();
    const line = JSON.stringify({ type: "delta", text: "split" });
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc.encode(line.slice(0, 7))); // a JSON line cut in half...
        c.enqueue(enc.encode(`${line.slice(7)}\n{"type":"comp`)); // ...finished, then another begun
        c.enqueue(enc.encode('lete"}\n'));
        c.close();
      },
    });
    fetchReturning(new Response(body, { status: 200 }));
    const seen: unknown[] = [];
    await apiStream("/api/x", (e) => seen.push(e));
    expect(seen).toEqual([{ type: "delta", text: "split" }, { type: "complete" }]);
  });

  it("maps an HTTP failure to the safe error, never exposing the body text", async () => {
    fetchReturning(envelope("unauthenticated", 401, "abcdefabcdefabcdefabcdefabcdefab"));
    const error = await rejection(apiStream("/api/x", vi.fn()));
    expect(error).toMatchObject({ status: 401, code: "unauthenticated", correlationId: "abcdefabcdefabcdefabcdefabcdefab" });
    expect(error.message).not.toContain("SERVER-TEXT");
  });

  it("treats a malformed line as an invalid response", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode("not json\n"));
        c.close();
      },
    });
    fetchReturning(new Response(body, { status: 200 }));
    expect(await rejection(apiStream("/api/x", vi.fn()))).toMatchObject({ code: "invalid_response" });
  });

  it("reports a connection that breaks part-way as a network error, after delivering what arrived", async () => {
    fetchReturning(ndjson([{ type: "delta", text: "partial" }], { breakAfter: true }));
    const seen: unknown[] = [];
    const error = await rejection(apiStream("/api/x", (e) => seen.push(e)));
    expect(error).toMatchObject({ status: null, code: "network_error" });
    expect(seen).toEqual([{ type: "delta", text: "partial" }]);
  });

  it("rethrows an abort as an abort, not as an API error", async () => {
    const controller = new AbortController();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")));
    controller.abort();
    await expect(apiStream("/api/x", vi.fn(), controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
});
