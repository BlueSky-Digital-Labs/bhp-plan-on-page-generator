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

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  if (!path.startsWith("/api/")) {
    throw new Error("apiRequest only calls same-origin /api/ paths");
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? "GET",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(null, "network_error", null);
  }

  const headerCorrelationId = safeCorrelationId(response.headers.get("X-Correlation-ID"));

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }

  if (!response.ok) {
    if (isEnvelope(body)) {
      const { code, correlation_id } = body.error;
      throw new ApiError(response.status, code, safeCorrelationId(correlation_id) ?? headerCorrelationId);
    }
    // Not our envelope (e.g. a gateway error page): keep only the status.
    throw new ApiError(response.status, "unexpected_response", headerCorrelationId);
  }

  if (body === undefined) {
    throw new ApiError(response.status, "invalid_response", headerCorrelationId);
  }
  return body as T;
}
