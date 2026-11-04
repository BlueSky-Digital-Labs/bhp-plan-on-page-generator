import { useCallback, useEffect, useRef, useState } from "react";
import { type CanvasView, getConversation } from "../api/conversation";
import { ApiError } from "../api/client";
import { downloadOnePagerPdf } from "../canvas/downloadPdf";
import { ErrorNotice } from "./ErrorNotice";
import { Loading } from "./Loading";
import { OnePager } from "./OnePager";

const PDF_FILENAME = "plan-on-a-page.pdf";

type State =
  | { phase: "loading" }
  | { phase: "ready"; canvas: CanvasView }
  | { phase: "incomplete"; remaining: number }
  | { phase: "error"; error: ApiError };

// The finished canvas as a one-pager. It only displays what the server has recorded, and only when
// the server's validator says the canvas is ready: nothing is generated or filled in here.
export function ReviewScreen({
  onBack,
  onAccessLost,
}: {
  onBack: () => void;
  onAccessLost: () => void;
}) {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [downloading, setDownloading] = useState(false);
  const [downloadFailed, setDownloadFailed] = useState(false);
  const pagerRef = useRef<HTMLDivElement>(null);

  const download = async () => {
    const onePager = pagerRef.current?.querySelector<HTMLElement>(".one-pager");
    if (!onePager) return;
    setDownloading(true);
    setDownloadFailed(false);
    try {
      await downloadOnePagerPdf(onePager, PDF_FILENAME);
    } catch {
      setDownloadFailed(true);
    } finally {
      setDownloading(false);
    }
  };

  const load = useCallback(async (signal?: AbortSignal) => {
    setState({ phase: "loading" });
    try {
      const { canvas } = await getConversation(signal);
      if (signal?.aborted) return;
      if (canvas?.ready_for_review) setState({ phase: "ready", canvas });
      else setState({ phase: "incomplete", remaining: canvas?.missing_required.length ?? 0 });
    } catch (error) {
      if (signal?.aborted) return;
      const apiError = error instanceof ApiError ? error : new ApiError(null, "client_error", null);
      if (apiError.status === 401) onAccessLost();
      else setState({ phase: "error", error: apiError });
    }
  }, [onAccessLost]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  if (state.phase === "loading") return <Loading label="Preparing your canvas…" />;
  if (state.phase === "error") return <ErrorNotice error={state.error} onRetry={() => void load()} />;
  if (state.phase === "incomplete") {
    return (
      <>
        <h1>Your canvas is not complete yet</h1>
        <p>
          {state.remaining > 0
            ? `${state.remaining} required answer${state.remaining === 1 ? " is" : "s are"} still to go.`
            : "A few last questions are still to go."}{" "}
          Finish the conversation first, then generate the canvas.
        </p>
        <button type="button" onClick={onBack}>
          Back to the conversation
        </button>
      </>
    );
  }
  return (
    <>
      <div className="review-actions">
        <button type="button" className="button--secondary" onClick={onBack}>
          Back to the conversation
        </button>
        <button type="button" onClick={() => void download()} disabled={downloading}>
          {downloading ? "Preparing PDF…" : "Download PDF"}
        </button>
      </div>
      <p className="review-note">
        “Download PDF” saves the canvas to your device as {PDF_FILENAME}. Open the file to check it before you rely
        on it. Nothing is submitted anywhere.
      </p>
      {downloadFailed && (
        <p role="alert" className="notice">
          The PDF could not be created. Please try again.
        </p>
      )}
      <div ref={pagerRef}>
        <OnePager canvas={state.canvas} />
      </div>
    </>
  );
}
