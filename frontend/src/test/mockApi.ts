import { vi } from "vitest";

export type Handler = () => Response | Promise<Response>;

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export const envelope = (code: string, status: number, correlationId = "0123456789abcdef0123456789abcdef") =>
  json({ error: { code, message: "SERVER-TEXT-MUST-NEVER-RENDER", correlation_id: correlationId } }, status);

export const SESSION = {
  created_at: "2026-06-01T09:00:00+00:00",
  expires_at: "2026-06-02T09:00:00+00:00",
};

/**
 * Stubs fetch with per-route handlers keyed "METHOD /api/path". A handler may be an array to
 * serve successive responses (the last one repeats). Unrouted calls fail the test loudly.
 */
export function mockApi(routes: Record<string, Handler | Handler[]>) {
  const queues = new Map(Object.entries(routes).map(([k, v]) => [k, Array.isArray(v) ? [...v] : [v]]));
  const calls: Array<{ key: string; init?: RequestInit }> = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    calls.push({ key, init });
    const queue = queues.get(key);
    if (!queue) throw new Error(`Unmocked request: ${key}`);
    const handler = queue.length > 1 ? queue.shift()! : queue[0];
    return handler();
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

export const csrfRoute = () => json({ csrf_token: "test-csrf-token" });
