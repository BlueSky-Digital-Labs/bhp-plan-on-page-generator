import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

export type RequestState<T> =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "success"; data: T }
  | { phase: "error"; error: ApiError };

// Runs one request at a time and ignores results that are superseded or arrive after
// unmount. Built on React state only; add a data library when a feature needs caching.
export function useRequest<T>(fetcher: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<RequestState<T>>({ phase: "idle" });
  const controller = useRef<AbortController | null>(null);

  const run = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setState({ phase: "loading" });
    try {
      const data = await fetcher(current.signal);
      if (!current.signal.aborted) setState({ phase: "success", data });
    } catch (error) {
      if (current.signal.aborted) return;
      setState({
        phase: "error",
        error: error instanceof ApiError ? error : new ApiError(null, "client_error", null),
      });
    }
  }, [fetcher]);

  useEffect(() => () => controller.current?.abort(), []);

  return { state, run };
}
