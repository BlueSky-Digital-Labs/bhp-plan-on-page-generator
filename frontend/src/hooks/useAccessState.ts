import { useCallback, useEffect, useState } from "react";
import { getAccessState } from "../api/access";
import { ApiError } from "../api/client";

export type AccessCheck =
  | { phase: "checking" }
  | { phase: "ready"; permitted: boolean }
  | { phase: "error"; error: ApiError };

// Asks the backend, which is authoritative, whether this browser may start a canvas.
// Until it answers, callers render only a loading state, never protected content.
export function useAccessState() {
  const [state, setState] = useState<AccessCheck>({ phase: "checking" });

  const check = useCallback(async (signal?: AbortSignal) => {
    setState({ phase: "checking" });
    try {
      const result = await getAccessState(signal);
      if (!signal?.aborted) setState({ phase: "ready", permitted: result.start_permitted === true });
    } catch (error) {
      if (signal?.aborted) return;
      setState({
        phase: "error",
        error: error instanceof ApiError ? error : new ApiError(null, "client_error", null),
      });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void check(controller.signal);
    return () => controller.abort();
  }, [check]);

  return { state, recheck: () => check() };
}
