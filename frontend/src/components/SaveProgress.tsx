import { useCallback, useState } from "react";
import { type AppendixFormat, type CanvasFormat, downloadAppendix, downloadCanvas } from "../api/appendix";
import { ApiError } from "../api/client";
import { DownloadMenu } from "./DownloadMenu";
import { DownloadStatus } from "./DownloadStatus";

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
const MESSAGES: Record<string, string> = {
  nothing_to_download: "Answer at least one question first, then download.",
  turn_in_progress: "The assistant is still replying. Please wait a moment and try again.",
  conversation_changed: "Your conversation changed. Please try again.",
  revision_conflict: "Your conversation changed since this page was shown. Please try again.",
};
const FAILED = "That could not be done. Please try again.";

// For someone who cannot finish now: download the canvas and the appendix as they are (marked as an
// unfinished draft), then stop. Stopping deletes the session on the server too. Later, a new session
// plus one of the downloaded files, attached to the first answer, carries on from there. The
// server's gate decides when stopping is allowed; this only shows it.
export function SaveProgress({
  revision,
  onStopped,
  onAccessLost,
  onOutOfDate,
}: {
  revision: number;
  onStopped: () => void;
  onAccessLost: () => void;
  onOutOfDate: () => void;
}) {
  const [busy, setBusy] = useState<"canvas" | "appendix" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [attempts, setAttempts] = useState(0); // each download attempt re-reads the status

  const run = useCallback(
    async (which: "canvas" | "appendix", action: () => Promise<void>) => {
      setBusy(which);
      setNotice(null);
      try {
        await action();
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return onAccessLost();
        const code = error instanceof ApiError ? error.code : "";
        setNotice(MESSAGES[code] ?? FAILED);
      } finally {
        setBusy(null);
        setAttempts((n) => n + 1);
      }
    },
    [onAccessLost],
  );

  return (
    <DownloadStatus
      variant="draft"
      revision={revision}
      refreshKey={attempts}
      onOutOfDate={onOutOfDate}
      onPurged={onStopped}
      onAccessLost={onAccessLost}
    >
      <p>
        You do not have to finish now. Download both files as they are, then stop the session. Later, start a new session
        and attach either file to your first answer: the assistant carries on with what is still missing.
      </p>
      <div className="dl__actions">
        <DownloadMenu
          label="Download canvas so far"
          choices={CANVAS_CHOICES}
          busy={busy === "canvas"}
          onChoose={(key) => void run("canvas", () => downloadCanvas(key as CanvasFormat, revision))}
        />
        <DownloadMenu
          label="Download appendix so far"
          choices={APPENDIX_CHOICES}
          busy={busy === "appendix"}
          onChoose={(key) => void run("appendix", () => downloadAppendix(key as AppendixFormat, revision))}
        />
      </div>
      {notice && (
        <p role="alert" className="notice">
          {notice}
        </p>
      )}
    </DownloadStatus>
  );
}
