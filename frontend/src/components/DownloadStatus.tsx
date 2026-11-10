import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { type DownloadStatus as Status, getDownloadStatus, purgeSession } from "../api/downloads";
import { ConfirmDialog } from "./ConfirmDialog";

// A download can finish just after the first status check, so after each download attempt the
// status is read again a few times, then left until the next attempt (or "Check again" after a failed read).
const FOLLOW_UPS_MS = [1500, 4000, 10000];

const OUT_OF_DATE_PURGE = "The canvas changed since it was shown. Please read it again before continuing.";

// The same panel serves a finished canvas ("final": delete the session) and an unfinished one the
// user is saving to carry on later ("draft": stop the session). Only the wording differs; the
// server's gate is the same rule for both.
const COPY = {
  final: {
    heading: "Your documents",
    failedCheck: "We could not check the download status. Deleting stays locked until it has been checked.",
    open: "Delete my session…",
    confirmTitle: "Delete this session now?",
    confirmBody: "Your conversation, canvas and documents are removed from the server and cannot be recovered.",
    lockedHint: "Download the one-page canvas, or submit it by email, first.",
    confirmYes: "Yes, delete everything",
    confirmNo: "Keep my session",
    working: "Deleting…",
    purgeLocked: "Deleting is still locked. Please download the one-page canvas or submit it by email first.",
  },
  draft: {
    heading: "Save your progress",
    failedCheck: "We could not check the download status. Stopping stays locked until it has been checked.",
    open: "Stop session…",
    confirmTitle: "Stop this session now?",
    confirmBody:
      "Your conversation and draft are removed from the server and cannot be recovered. To carry on, you will start a new session and attach one of the files you downloaded.",
    lockedHint: "Download the one-page canvas first.",
    confirmYes: "Yes, stop and delete everything",
    confirmNo: "Keep going",
    working: "Stopping…",
    purgeLocked: "Stopping is still locked. Please download the one-page canvas first.",
  },
} as const;
const FAILED = "That could not be done. Please try again.";

type State =
  | { phase: "checking" }
  | { phase: "ready"; status: Status }
  | { phase: "failed" };

// Shows, for the current revision, whether the server has finished sending the one-page canvas, and the
// delete action, which unlocks only when the server says it was sent. "Downloaded" means the
// server sent the whole file: it cannot know whether the browser kept it or the user saved it.
export function DownloadStatus({
  revision,
  refreshKey,
  variant = "final",
  children,
  onOutOfDate,
  onPurged,
  onAccessLost,
}: {
  revision: number;
  refreshKey: number; // bumped by the page after each download attempt
  variant?: "final" | "draft";
  children?: ReactNode; // shown under the heading, e.g. the download buttons
  onOutOfDate: () => void;
  onPurged: () => void;
  onAccessLost: () => void;
}) {
  const copy = COPY[variant];
  const [state, setState] = useState<State>({ phase: "checking" });
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const live = useRef(true);
  const latest = useRef(0); // only the newest check may update the screen

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    const mine = ++latest.current;
    setState({ phase: "checking" }); // nothing stale stays eligible while this is in flight
    try {
      const status = await getDownloadStatus();
      if (!live.current || mine !== latest.current) return;
      if (status.revision !== revision) return onOutOfDate(); // answered again, in another tab
      setState({ phase: "ready", status });
      if (!status.purge_eligible) setConfirming(false);
    } catch (error) {
      if (!live.current || mine !== latest.current) return;
      if (error instanceof ApiError && error.status === 401) return onAccessLost();
      setState({ phase: "failed" });
      setConfirming(false);
    }
  }, [revision, onOutOfDate, onAccessLost]);

  // Once on arrival, and again (with two short follow-ups) after each download attempt.
  useEffect(() => {
    void refresh();
    if (refreshKey === 0) return;
    const timers = FOLLOW_UPS_MS.map((ms) => window.setTimeout(() => void refresh(), ms));
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [refresh, refreshKey]);

  const remove = async () => {
    setDeleting(true);
    setNotice(null);
    try {
      await purgeSession(revision);
      if (live.current) onPurged();
    } catch (error) {
      if (!live.current) return;
      if (error instanceof ApiError && error.status === 401) return onAccessLost();
      const code = error instanceof ApiError ? error.code : "";
      setNotice(code === "purge_locked" ? copy.purgeLocked : code === "revision_conflict" ? OUT_OF_DATE_PURGE : FAILED);
      setConfirming(false);
      if (code === "revision_conflict") onOutOfDate();
      else void refresh();
    } finally {
      if (live.current) setDeleting(false);
    }
  };

  const status = state.phase === "ready" ? state.status : null;
  const eligible = Boolean(status?.purge_eligible);
  const dialog = confirming && eligible && (
    <ConfirmDialog
      title={copy.confirmTitle}
      body={copy.confirmBody}
      confirmLabel={copy.confirmYes}
      cancelLabel={copy.confirmNo}
      workingLabel={copy.working}
      working={deleting}
      onConfirm={() => void remove()}
      onCancel={() => setConfirming(false)}
    />
  );

  const stopButton = (
    <button
      type="button"
      className="button--danger dl__stop"
      disabled={!eligible}
      aria-describedby={eligible ? undefined : "dl-stop-hint"}
      title={eligible ? undefined : copy.lockedHint}
      onClick={() => setConfirming(true)}
    >
      {copy.open}
    </button>
  );
  const stopHint = !eligible && (
    <span id="dl-stop-hint" className="sr-only">
      {copy.lockedHint}
    </span>
  );

  if (variant === "draft") {
    // The half-way card is just the download buttons and the way to stop, which unlocks the moment the
    // one-page canvas has been downloaded. Everything else is left out to keep it small.
    return (
      <section className="dl dl--bare card" aria-labelledby="dl-heading">
        <h2 id="dl-heading" className="sr-only">
          {copy.heading}
        </h2>
        {children}
        {stopButton}
        {stopHint}
        {state.phase === "failed" && (
          <p role="alert" className="notice">
            {copy.failedCheck}{" "}
            <button type="button" className="button--secondary" onClick={() => void refresh()}>
              Check again
            </button>
          </p>
        )}
        {notice && (
          <p role="alert" className="notice">
            {notice}
          </p>
        )}
        {dialog}
      </section>
    );
  }

  // On the review page: the optional downloads (the children) and the way to delete, in one group on
  // the right of the main button. The delete button is styled like the downloads and unlocks once the
  // one-page canvas has been downloaded or the submission has been emailed.
  return (
    <section className="dl dl--inline" aria-labelledby="dl-heading">
      <h2 id="dl-heading" className="sr-only">
        {copy.heading}
      </h2>
      {children}
      <button
        type="button"
        className="button--secondary dl__stop"
        disabled={!eligible}
        aria-describedby={eligible ? undefined : "dl-stop-hint"}
        title={eligible ? undefined : copy.lockedHint}
        onClick={() => setConfirming(true)}
      >
        {copy.open}
      </button>
      {stopHint}
      {state.phase === "failed" && (
        <p role="alert" className="notice dl__wide">
          {copy.failedCheck}{" "}
          <button type="button" className="button--secondary" onClick={() => void refresh()}>
            Check again
          </button>
        </p>
      )}
      {notice && (
        <p role="alert" className="notice dl__wide">
          {notice}
        </p>
      )}
      {dialog}
    </section>
  );
}
