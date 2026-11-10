import { apiRequest } from "./client";

export interface SessionInfo {
  created_at: string;
  expires_at: string;
  // Seconds until the session ends for want of activity (15 minutes after the last thing the user
  // did). Absent from an older server, in which case there is no idle limit to show.
  idle_seconds_remaining?: number;
}

// The session cookie is HttpOnly: the browser holds it, page JavaScript never sees it.
export function getSession(signal?: AbortSignal): Promise<SessionInfo> {
  return apiRequest<SessionInfo>("/api/session", { signal });
}

// Boolean only. The cookies are HttpOnly, so the page asks the server instead of reading them.
export function getAccessState(signal?: AbortSignal): Promise<{ start_permitted: boolean }> {
  return apiRequest<{ start_permitted: boolean }>("/api/access-state", { signal });
}

export function submitAccessCode(code: string): Promise<{ status: string }> {
  return apiRequest<{ status: string }>("/api/access", {
    method: "POST",
    form: { access_code: code },
    csrf: true,
  });
}

export interface StartedSession extends SessionInfo {
  next: string; // server-authorised route to continue to
}

export function startSession(): Promise<StartedSession> {
  return apiRequest<StartedSession>("/api/sessions", { method: "POST", csrf: true });
}

// "Keep my session": counts as activity, so the idle clock starts again.
export function keepSession(): Promise<SessionInfo> {
  return apiRequest<SessionInfo>("/api/session/keepalive", { method: "POST", csrf: true });
}

export function endSession(): Promise<void> {
  return apiRequest<void>("/api/session", { method: "DELETE", csrf: true, noContent: true });
}
