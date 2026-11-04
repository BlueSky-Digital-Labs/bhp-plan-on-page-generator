import { useCallback, useEffect, useRef, useState } from "react";
import { type CanvasView, getConversation } from "../api/conversation";
import { ApiError } from "../api/client";
import { type AppendixFormat, downloadAppendix, downloadCanvasPptx } from "../api/appendix";
import { downloadOnePagerPdf } from "../canvas/downloadPdf";
import { DownloadMenu } from "./DownloadMenu";
import { ErrorNotice } from "./ErrorNotice";
import { Loading } from "./Loading";
import { OnePager } from "./OnePager";

const PDF_FILENAME = "plan-on-a-page.pdf";

const CANVAS_CHOICES = [
  { key: "pdf", label: "PDF" },
  { key: "pptx", label: "PowerPoint (.pptx)" },
];
const APPENDIX_CHOICES = [
  { key: "pdf", label: "PDF" },
  { key: "docx", label: "Word (.docx)" },
  { key: "json", label: "JSON" },
];
// Fixed copy keyed on the error code: nothing from the response is shown.
const APPENDIX_MESSAGES: Record<string, string> = {
  canvas_not_ready: "The canvas is not complete yet, so the appendix is not available.",
  conversation_changed: "Your conversation changed while the appendix was being prepared. Please try again.",
};
const PPTX_FAILED = "The PowerPoint file could not be created. Please try again.";
const APPENDIX_FAILED = "The appendix could not be created. Please try again.";

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
  const [pptxBusy, setPptxBusy] = useState(false);
  const [pptxError, setPptxError] = useState<string | null>(null);
  const [appendixBusy, setAppendixBusy] = useState(false);
  const [appendixError, setAppendixError] = useState<string | null>(null);
  const pagerRef = useRef<HTMLDivElement>(null);

  const getPptx = async () => {
    setPptxBusy(true);
    setPptxError(null);
    try {
      await downloadCanvasPptx();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) onAccessLost();
      else setPptxError((error instanceof ApiError && APPENDIX_MESSAGES[error.code]) || PPTX_FAILED);
    } finally {
      setPptxBusy(false);
    }
  };

  const getAppendix = async (format: AppendixFormat) => {
    setAppendixBusy(true);
    setAppendixError(null);
    try {
      await downloadAppendix(format);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) onAccessLost();
      else setAppendixError((error instanceof ApiError && APPENDIX_MESSAGES[error.code]) || APPENDIX_FAILED);
    } finally {
      setAppendixBusy(false);
    }
  };

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
        <DownloadMenu
          label="Download canvas"
          choices={CANVAS_CHOICES}
          busy={downloading || pptxBusy}
          onChoose={(key) => void (key === "pptx" ? getPptx() : download())}
        />
        <DownloadMenu
          label="Download appendix"
          choices={APPENDIX_CHOICES}
          busy={appendixBusy}
          onChoose={(key) => void getAppendix(key as AppendixFormat)}
        />
      </div>
      <p className="review-note">
        The canvas is the one-page summary ({PDF_FILENAME}, or an editable PowerPoint slide). The appendix holds
        a quick summary, the full answers and the whole conversation, for assessors, and runs to as many pages as it needs. Open a
        file to check it before you rely on it. Nothing is submitted anywhere.
      </p>
      {pptxError && (
        <p role="alert" className="notice">
          {pptxError}
        </p>
      )}
      {downloadFailed && (
        <p role="alert" className="notice">
          The PDF could not be created. Please try again.
        </p>
      )}
      {appendixError && (
        <p role="alert" className="notice">
          {appendixError}
        </p>
      )}
      <div ref={pagerRef}>
        <OnePager canvas={state.canvas} />
      </div>
    </>
  );
}
