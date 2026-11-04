import { type FormEvent, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { MAX_NOTE_CHARS, MAX_RECIPIENTS, looksLikeAddress, parseRecipients, sendCanvasEmail } from "../api/email";

// Fixed copy keyed on the error code: nothing from the response is shown.
const MESSAGES: Record<string, string> = {
  invalid_recipient: "Please check the email addresses and try again.",
  invalid_request: "Please check the email addresses and try again.",
  recipient_not_allowed: "One of those addresses cannot be used. Please use a company address.",
  email_unavailable: "Email is not switched on yet. You can still download the documents.",
  email_limit: "You have sent the most emails allowed for this session.",
  send_in_progress: "An email is already being sent. Please wait a moment.",
  email_busy: "The email service is busy. Please try again shortly.",
  email_rejected: "The email service did not accept it. Please check the addresses and try again.",
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

// Sends the confirmed canvas and appendix, as two PDFs the server builds, to the addresses typed.
// The user can add a note; everything else about the email is fixed by the server.
export function EmailPanel({
  revision,
  onClose,
  onOutOfDate,
  onAccessLost,
}: {
  revision: number;
  onClose: () => void;
  onOutOfDate: (error: ApiError) => void;
  onAccessLost: () => void;
}) {
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string[] | null>(null);
  const toInput = useRef<HTMLInputElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => toInput.current?.focus(), []);
  useEffect(() => {
    if (sentTo) closeButton.current?.focus();
  }, [sentTo]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (sending) return;
    const recipients = parseRecipients(to);
    if (recipients.length === 0) return setError("Please enter at least one email address.");
    if (recipients.length > MAX_RECIPIENTS) return setError(`Please use no more than ${MAX_RECIPIENTS} addresses.`);
    if (!recipients.every(looksLikeAddress)) return setError(MESSAGES.invalid_recipient);
    setSending(true);
    setError(null);
    try {
      await sendCanvasEmail({ revision, to: recipients, message: note });
      setSentTo(recipients);
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : "";
      if (caught instanceof ApiError && caught.status === 401) return onAccessLost();
      if (caught instanceof ApiError && OUT_OF_DATE.has(code)) return onOutOfDate(caught);
      setError(MESSAGES[code] ?? FAILED);
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="mail card" aria-labelledby="mail-heading">
      <h2 id="mail-heading">Send by email</h2>
      {sentTo ? (
        <>
          <p role="status" className="readback__done">
            Sent to {sentTo.join(", ")}, with the one-page canvas and the appendix attached as PDFs.
          </p>
          <div className="mail__actions">
            <button type="button" ref={closeButton} onClick={onClose}>
              Done
            </button>
          </div>
        </>
      ) : (
        <form onSubmit={(event) => void submit(event)} noValidate>
          <p>
            We will create the one-page canvas and the appendix as PDFs and attach both. Nothing is saved on the server.
          </p>
          <div className="field">
            <label htmlFor="mail-to">To</label>
            <input
              id="mail-to"
              ref={toInput}
              type="text"
              inputMode="email"
              autoComplete="off"
              spellCheck={false}
              value={to}
              onChange={(event) => setTo(event.target.value)}
              aria-describedby="mail-to-help"
            />
            <p id="mail-to-help" className="field__help">
              One or more addresses, separated by commas. Up to {MAX_RECIPIENTS}.
            </p>
          </div>
          <div className="field">
            <label htmlFor="mail-note">Message (optional)</label>
            <textarea
              id="mail-note"
              rows={4}
              maxLength={MAX_NOTE_CHARS}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              aria-describedby="mail-note-help"
            />
            <p id="mail-note-help" className="field__help">
              Added to the email exactly as you write it.
            </p>
          </div>
          {error && (
            <p role="alert" className="notice">
              {error}
            </p>
          )}
          <div className="mail__actions">
            <button type="submit" disabled={sending}>
              {sending ? "Sending…" : "Send email"}
            </button>
            <button type="button" className="button--secondary" onClick={onClose} disabled={sending}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
