import { useCallback, useEffect, useState } from "react";
import { type CanvasView, type Conversation, confirmCanvas, getConversation } from "../api/conversation";
import { ApiError } from "../api/client";
import { useSubmitByEmail } from "../hooks/useSubmitByEmail";
import { type AppendixFormat, type CanvasFormat, downloadAppendix, downloadCanvas } from "../api/appendix";
import { DownloadMenu } from "./DownloadMenu";
import { DownloadStatus } from "./DownloadStatus";
import { ErrorNotice } from "./ErrorNotice";
import { Loading } from "./Loading";
import { OnePager } from "./OnePager";

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
const CHANGED = "The canvas changed since it was shown. Please read it again before confirming.";
const MESSAGES: Record<string, string> = {
  revision_conflict: CHANGED,
  not_confirmed: "Please confirm the canvas first.",
  canvas_not_ready: "The canvas is not complete yet.",
  canvas_incomplete: "The canvas is not complete yet.",
  conversation_changed: CHANGED,
  turn_in_progress: "The assistant has not finished replying yet. Please try again in a moment.",
};
// After these the page is out of date, so it is re-read from the server straight away.
const OUT_OF_DATE = new Set(Object.keys(MESSAGES));
const FAILED = "That could not be done. Please try again.";

type State =
  | { phase: "loading" }
  | { phase: "ready"; canvas: CanvasView; revision: number; confirmedRevision: number | null }
  | { phase: "incomplete"; remaining: number }
  | { phase: "error"; error: ApiError };

function stateFrom({ canvas, revision, confirmed_revision }: Conversation): State {
  if (canvas?.ready_for_review) {
    return { phase: "ready", canvas, revision, confirmedRevision: confirmed_revision ?? null };
  }
  return { phase: "incomplete", remaining: canvas?.missing_required.length ?? 0 };
}

// The read-back: the finished canvas, shown only when the server says it is complete. The user reads
// every field, corrects anything through the conversation, and confirms the exact revision shown.
// Documents are offered only for a revision that is both current and confirmed; the server checks
// that again on every download. Nothing is generated or filled in here.
export function ReviewScreen({
  onBack,
  onAccessLost,
  onPurged,
}: {
  onBack: () => void;
  onAccessLost: () => void;
  onPurged: () => void;
}) {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<"canvas" | "appendix" | null>(null);
  const [downloadAttempts, setDownloadAttempts] = useState(0); // each one re-reads the status

  // `quiet` re-reads without blanking the screen (after a stale attempt).
  const load = useCallback(
    async (signal?: AbortSignal, quiet = false) => {
      if (!quiet) setState({ phase: "loading" });
      try {
        const conversation = await getConversation(signal);
        if (signal?.aborted) return;
        setState(stateFrom(conversation));
      } catch (error) {
        if (signal?.aborted) return;
        const apiError = error instanceof ApiError ? error : new ApiError(null, "client_error", null);
        if (apiError.status === 401) onAccessLost();
        else setState({ phase: "error", error: apiError });
      }
    },
    [onAccessLost],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  // One place for every failed action: sign-in lost, out of date (re-read at once), or generic.
  const failed = (error: unknown) => {
    if (error instanceof ApiError && error.status === 401) return onAccessLost();
    const code = error instanceof ApiError ? error.code : "";
    setNotice(MESSAGES[code] ?? FAILED);
    if (OUT_OF_DATE.has(code)) void load(undefined, true);
  };

  // The canvas moved on (another tab, a later answer): say so and read it again.
  const outOfDate = useCallback(() => {
    setNotice(CHANGED);
    void load(undefined, true);
  }, [load]);

  // One click submits the confirmed canvas and appendix by email. It is called here, before the
  // early returns below, so the hook order never changes; with no canvas yet it is never used.
  const email = useSubmitByEmail({
    revision: state.phase === "ready" ? state.revision : -1,
    onOutOfDate: failed,
    onAccessLost,
  });

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
        {notice && (
          <p role="alert" className="notice">
            {notice}
          </p>
        )}
        <button type="button" onClick={onBack}>
          Back to the conversation
        </button>
      </>
    );
  }

  const { canvas, revision, confirmedRevision } = state;
  const confirmed = confirmedRevision === revision;
  const priority = canvas.fields.priority;

  const confirm = async () => {
    setConfirming(true);
    setNotice(null);
    try {
      setState(stateFrom(await confirmCanvas(revision)));
    } catch (error) {
      failed(error);
    } finally {
      setConfirming(false);
    }
  };

  const getCanvas = async (format: CanvasFormat) => {
    setBusy("canvas");
    setNotice(null);
    try {
      await downloadCanvas(format, revision);
    } catch (error) {
      failed(error);
    } finally {
      setBusy(null);
      setDownloadAttempts((n) => n + 1);
    }
  };

  const getAppendix = async (format: AppendixFormat) => {
    setBusy("appendix");
    setNotice(null);
    try {
      await downloadAppendix(format, revision);
    } catch (error) {
      failed(error);
    } finally {
      setBusy(null);
      setDownloadAttempts((n) => n + 1);
    }
  };

  return (
    <>
      <div className="review-actions">
        {/* Once confirmed the canvas is final: there is no way back to change it. */}
        {!confirmed && (
          <button type="button" className="button--secondary" onClick={onBack}>
            Back to the conversation
          </button>
        )}
        {confirmed && (
          <>
            <DownloadMenu
              label="Download canvas"
              choices={CANVAS_CHOICES}
              busy={busy === "canvas"}
              onChoose={(key) => void getCanvas(key as CanvasFormat)}
            />
            <DownloadMenu
              label="Download appendix"
              choices={APPENDIX_CHOICES}
              busy={busy === "appendix"}
              onChoose={(key) => void getAppendix(key as AppendixFormat)}
            />
            <button type="button" onClick={() => void email.submit()} disabled={email.sending}>
              {email.sending ? "Submitting…" : "Submit by email"}
            </button>
          </>
        )}
      </div>

      {confirmed && (email.sent || email.error) && (
        <section className="mail card" aria-label="Email submission">
          {email.sent && (
            <p role="status" className="readback__done">
              Submitted by email. The one-page canvas and the appendix were sent as two PDF attachments.
            </p>
          )}
          {email.error && (
            <p role="alert" className="notice">
              {email.error}
            </p>
          )}
        </section>
      )}

      {confirmed && (
        <DownloadStatus
          revision={revision}
          refreshKey={downloadAttempts}
          onOutOfDate={outOfDate}
          onPurged={onPurged}
          onAccessLost={onAccessLost}
        />
      )}

      <section className="readback card" aria-labelledby="readback-heading">
        <h2 id="readback-heading">Check your canvas</h2>
        <p>
          Read every section below. You are responsible for the facts being right
          {typeof priority === "string" && priority ? (
            <>
              {" "}
              and for the priority, <strong>{priority}</strong>, which is the one you chose
            </>
          ) : null}
          . {confirmed ? "" : "If anything is wrong, go back and tell the assistant what to change, before you confirm."}
        </p>
        {confirmed ? (
          <p role="status" className="readback__done">
            You confirmed this version, so it is final and cannot be changed. You can download it, or submit it by email, with the
            buttons above. Downloads are saved on your own device. Submitting sends both documents as PDFs to one fixed address: you
            cannot change who receives it or what it says.
          </p>
        ) : (
          <div className="readback__actions">
            <button type="button" onClick={() => void confirm()} disabled={confirming}>
              {confirming ? "Confirming…" : "Yes, this is correct"}
            </button>
            <button type="button" className="button--secondary" onClick={onBack}>
              Something is wrong: go back and correct it
            </button>
          </div>
        )}
        {notice && (
          <p role="alert" className="notice">
            {notice}
          </p>
        )}
      </section>

      <OnePager canvas={canvas} />
    </>
  );
}
