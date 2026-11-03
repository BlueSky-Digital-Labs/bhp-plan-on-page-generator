import { apiRequest } from "./client";

export interface SessionInfo {
  created_at: string;
  expires_at: string;
}

// The session cookie is HttpOnly: the browser holds it, page JavaScript never sees it.
export function getSession(signal?: AbortSignal): Promise<SessionInfo> {
  return apiRequest<SessionInfo>("/api/session", { signal });
}

export function submitAccessCode(code: string): Promise<{ status: string }> {
  return apiRequest<{ status: string }>("/api/access", {
    method: "POST",
    form: { access_code: code },
    csrf: true,
  });
}

export function startSession(): Promise<SessionInfo> {
  return apiRequest<SessionInfo>("/api/sessions", { method: "POST", csrf: true });
}

export function endSession(): Promise<void> {
  return apiRequest<void>("/api/session", { method: "DELETE", csrf: true, noContent: true });
}
