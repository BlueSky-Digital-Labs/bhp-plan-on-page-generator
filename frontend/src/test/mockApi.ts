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
    // The page asks for its configured names on every start; tests only list it if they care.
    if (key === "GET /api/branding" && !queues.has(key)) return json(DEFAULT_BRANDING_BODY);
    calls.push({ key, init });
    const queue = queues.get(key);
    if (!queue) throw new Error(`Unmocked request: ${key}`);
    const handler = queue.length > 1 ? queue.shift()! : queue[0];
    return handler();
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

export const DEFAULT_BRANDING_BODY = { app_name: "SCP AI Ideation assistant", header_text: "SCP AI Ideation assistant", footer_text: "BHP" };

export const csrfRoute = () => json({ csrf_token: "test-csrf-token" });

const encoder = new TextEncoder();

/** A whole NDJSON stream response in one go. `breakAfter` makes the connection fail at the end. */
export function ndjson(events: unknown[], options: { breakAfter?: boolean } = {}): Response {
  let sent = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (sent < events.length) controller.enqueue(encoder.encode(`${JSON.stringify(events[sent++])}\n`));
      else if (options.breakAfter) controller.error(new TypeError("connection lost"));
      else controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "application/x-ndjson" } });
}

/** A stream the test drives by hand, so it can look at the screen between events. */
export function controlledStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start: (c) => (controller = c) });
  return {
    response: () => new Response(body, { status: 200, headers: { "Content-Type": "application/x-ndjson" } }),
    push: (event: unknown) => controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`)),
    end: () => controller.close(),
    fail: () => controller.error(new TypeError("connection lost")),
  };
}
