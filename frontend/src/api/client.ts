// Typed request helper for the FastAPI JSON API.
//
// Contract (see docs/frontend-contract.md):
//   - Requests are same-origin and relative ("/api/..."). There is no base URL setting.
//   - Success bodies are JSON.
//   - Failures are { error: { code, message, correlation_id } }.
//
// Failures are normalised into ApiError. The server `message` is deliberately not
// carried through: the UI decides what the user sees, so nothing a server (or a proxy
// in front of it) returns can reach the screen.

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    correlation_id: string;
  };
}

export type ApiErrorCode = string;

export class ApiError extends Error {
  readonly status: number | null;
  readonly code: ApiErrorCode;
  readonly correlationId: string | null;

  constructor(status: number | null, code: ApiErrorCode, correlationId: string | null) {
    super(code); // never a server-supplied message
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.correlationId = correlationId;
  }
}

// Correlation references are server-generated 32-char hex ids. Anything else is dropped,
// so an unexpected response body cannot inject text into the error UI.
const CORRELATION_ID = /^[a-f0-9]{32}$/;

function safeCorrelationId(value: unknown): string | null {
  return typeof value === "string" && CORRELATION_ID.test(value) ? value : null;
}

function isEnvelope(body: unknown): body is ApiErrorEnvelope {
  if (typeof body !== "object" || body === null || !("error" in body)) return false;
  const error = (body as { error: unknown }).error;
  return typeof error === "object" && error !== null && typeof (error as { code?: unknown }).code === "string";
}

function errorFromResponse(
  status: number,
  payload: unknown,
  headerCorrelationId: string | null,
): ApiError {
  if (isEnvelope(payload)) {
    const { code, correlation_id } = payload.error;
    return new ApiError(status, code, safeCorrelationId(correlation_id) ?? headerCorrelationId);
  }
  // Not our envelope (e.g. a gateway error page): keep only the status.
  return new ApiError(status, "unexpected_response", headerCorrelationId);
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  // application/x-www-form-urlencoded body (used by POST /api/access). Mutually exclusive with body.
  form?: Record<string, string>;
  // State-changing requests: fetch a CSRF token first and send it as X-CSRF-Token.
  csrf?: boolean;
  // The endpoint answers 204 with no body on success.
  noContent?: boolean;
  signal?: AbortSignal;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  if (!path.startsWith("/api/")) {
    throw new Error("apiRequest only calls same-origin /api/ paths");
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.csrf) {
    const { csrf_token } = await apiRequest<{ csrf_token: string }>("/api/csrf", {
      signal: options.signal,
    });
    headers["X-CSRF-Token"] = csrf_token;
  }
  let body: BodyInit | undefined;
  if (options.form) {
    body = new URLSearchParams(options.form); // the browser sets the form content type
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? "GET",
      credentials: "same-origin",
      headers,
      body,
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(null, "network_error", null);
  }

  const headerCorrelationId = safeCorrelationId(response.headers.get("X-Correlation-ID"));

  if (options.noContent && response.status === 204) return undefined as T;

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = undefined;
  }

  if (!response.ok) throw errorFromResponse(response.status, payload, headerCorrelationId);

  if (payload === undefined) {
    throw new ApiError(response.status, "invalid_response", headerCorrelationId);
  }
  return payload as T;
}

// Streamed JSON lines (application/x-ndjson) from a same-origin /api/ path. Each line is parsed
// and handed to `onEvent`; nothing is kept. Resolves when the server ends the stream, and
// rejects with ApiError for HTTP failures or a connection that breaks part-way. Callers must not
// treat an interrupted stream as proof of anything: re-read the authoritative state instead.
export async function apiStream(
  path: string,
  onEvent: (event: unknown) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!path.startsWith("/api/")) {
    throw new Error("apiStream only calls same-origin /api/ paths");
  }

  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      headers: { Accept: "application/x-ndjson" },
      signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(null, "network_error", null);
  }

  const headerCorrelationId = safeCorrelationId(response.headers.get("X-Correlation-ID"));
  if (!response.ok) {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      payload = undefined;
    }
    throw errorFromResponse(response.status, payload, headerCorrelationId);
  }
  if (!response.body) throw new ApiError(response.status, "invalid_response", headerCorrelationId);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  const flush = (line: string) => {
    if (!line.trim()) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      throw new ApiError(response.status, "invalid_response", headerCorrelationId);
    }
    onEvent(parsed);
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, { stream: true });
      const lines = buffered.split("\n");
      buffered = lines.pop() ?? "";
      lines.forEach(flush);
    }
    flush(buffered);
  } catch (cause) {
    if (cause instanceof ApiError) throw cause;
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(null, "network_error", headerCorrelationId); // broke mid-stream
  }
}
