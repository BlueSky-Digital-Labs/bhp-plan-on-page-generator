import { useCallback, useEffect, useState } from "react";
import { type SessionInfo, endSession, getSession } from "../api/access";
import { ApiError } from "../api/client";

export type AccessState =
  | { phase: "checking" }
  | { phase: "signed_out" }
  | { phase: "signed_in"; session: SessionInfo }
  | { phase: "error"; error: ApiError };

// Asks the server whether this browser already has a valid session (the cookie is HttpOnly,
// so the page cannot tell on its own). A 401 simply means "not signed in".
export function useAccess() {
  const [state, setState] = useState<AccessState>({ phase: "checking" });

  const check = useCallback(async (signal?: AbortSignal) => {
    setState({ phase: "checking" });
    try {
      const session = await getSession(signal);
      if (!signal?.aborted) setState({ phase: "signed_in", session });
    } catch (error) {
      if (signal?.aborted) return;
      if (error instanceof ApiError && error.status === 401) setState({ phase: "signed_out" });
      else setState({ phase: "error", error: error instanceof ApiError ? error : new ApiError(null, "client_error", null) });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void check(controller.signal);
    return () => controller.abort();
  }, [check]);

  const signedIn = useCallback((session: SessionInfo) => setState({ phase: "signed_in", session }), []);

  const signOut = useCallback(async () => {
    try {
      await endSession();
      setState({ phase: "signed_out" });
    } catch (error) {
      // 401 means the session was already gone; anything else keeps the user signed in and shows the error.
      if (error instanceof ApiError && error.status === 401) setState({ phase: "signed_out" });
      else setState({ phase: "error", error: error instanceof ApiError ? error : new ApiError(null, "client_error", null) });
    }
  }, []);

  return { state, check: () => check(), signedIn, signOut };
}
