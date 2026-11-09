import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { submitCanvasEmail } from "../api/email";

// Fixed copy keyed on the error code: nothing from the response is shown.
const MESSAGES: Record<string, string> = {
  email_unavailable: "Submitting by email is not switched on yet. You can still download the documents.",
  email_limit: "You have used all the email submissions allowed for this session.",
  send_in_progress: "A submission is already being sent. Please wait a moment.",
  email_busy: "The email service is busy. Please try again shortly.",
  email_rejected: "The email service did not accept it. Please try again later.",
  email_too_large: "The documents are too large to send by email. Please download them instead.",
  email_failed: "The email could not be sent. Please try again.",
};
const FAILED = "That could not be done. Please try again.";
// The canvas moved on since it was confirmed: the review page re-reads itself and says so.
const OUT_OF_DATE = new Set([
  "revision_conflict",
  "not_confirmed",
  "canvas_not_ready",
  "conversation_changed",
  "turn_in_progress",
]);

// One click submits the confirmed canvas and appendix by email: no address and no text to fill in.
// A new revision clears the last result, because it needs confirming and submitting again.
export function useSubmitByEmail({
  revision,
  onOutOfDate,
  onAccessLost,
}: {
  revision: number;
  onOutOfDate: (error: ApiError) => void;
  onAccessLost: () => void;
}) {
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);

  useEffect(() => {
    setSent(false);
    setError(null);
  }, [revision]);

  const submit = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setSending(true);
    setSent(false);
    setError(null);
    try {
      await submitCanvasEmail(revision);
      setSent(true);
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : "";
      if (caught instanceof ApiError && caught.status === 401) return onAccessLost();
      if (caught instanceof ApiError && OUT_OF_DATE.has(code)) return onOutOfDate(caught);
      setError(MESSAGES[code] ?? FAILED);
    } finally {
      busy.current = false;
      setSending(false);
    }
  }, [revision, onOutOfDate, onAccessLost]);

  return { submit, sending, sent, error };
}
