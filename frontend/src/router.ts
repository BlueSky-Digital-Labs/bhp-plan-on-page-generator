import { useEffect, useState } from "react";

// Three browser routes and no routing library. Direct navigation and refresh work because the
// backend serves the app for these paths; whether a route may be viewed is decided by the API.
export const ROUTES = { entry: "/", start: "/start", conversation: "/conversation" } as const;

export type NavigateOptions = { replace?: boolean; notice?: "access_required" };

export function navigate(to: string, { replace = false, notice }: NavigateOptions = {}) {
  const state = notice ? { notice } : null;
  if (replace) window.history.replaceState(state, "", to);
  else window.history.pushState(state, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function currentPath(): string {
  const path = window.location.pathname.replace(/\/+$/, "");
  return path === "" ? "/" : path;
}

export function usePath(): string {
  const [path, setPath] = useState(currentPath);
  useEffect(() => {
    const onChange = () => setPath(currentPath());
    window.addEventListener("popstate", onChange);
    // Child effects run before this one, so a redirect fired on first render would otherwise be
    // missed. Re-read the path now that we are subscribed.
    onChange();
    return () => window.removeEventListener("popstate", onChange);
  }, []);
  return path;
}

// One-time hint left by a redirect, e.g. "your access expired". Never carries user content.
export function readNotice(): "access_required" | null {
  const state = window.history.state as { notice?: unknown } | null;
  return state?.notice === "access_required" ? "access_required" : null;
}

// The server names where to go after a session is created. Only an in-app path is followed;
// anything else (an absolute or protocol-relative URL, odd characters) falls back to the
// conversation route, so a bad response can never redirect off-site.
export function safeRoute(next: unknown): string {
  return typeof next === "string" && /^\/[A-Za-z0-9_-]+(\/[A-Za-z0-9_-]+)*$/.test(next)
    ? next
    : ROUTES.conversation;
}
