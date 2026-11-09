import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { type DownloadStatus as Status, getDownloadStatus, purgeSession } from "../api/downloads";
import { ConfirmDialog } from "./ConfirmDialog";

// A download can finish just after the first status check, so after each download attempt the
// status is read again a couple of times, then left to the manual "Refresh status" button.
const FOLLOW_UPS_MS = [1500, 4000, 10000];

// Fixed copy keyed on the lock reason: nothing from the response is shown.
const OUTSTANDING: Record<string, string> = {
  canvas_not_downloaded: "the one-page canvas",
};
const OUT_OF_DATE_PURGE = "The canvas changed since it was shown. Please read it again before continuing.";

// The same panel serves a finished canvas ("final": delete the session) and an unfinished one the
// user is saving to carry on later ("draft": stop the session). Only the wording differs; the
// server's gate is the same rule for both.
const COPY = {
  final: {
    heading: "Your documents",
    locked: "Deleting is locked.",
    otherLock: "The current canvas must be confirmed and the one-page canvas downloaded first.",
    failedCheck: "We could not check the download status. Deleting stays locked until it has been checked.",
    ready: "The one-page canvas has been downloaded. You can delete this session when you are ready.",
    help: "Save the documents somewhere you can find them before you delete anything.",
    open: "Delete my session…",
    confirmTitle: "Delete this session now?",
    confirmBody: "Your conversation, canvas and documents are removed from the server and cannot be recovered.",
    lockedHint: "Download the one-page canvas first.",
    confirmYes: "Yes, delete everything",
    confirmNo: "Keep my session",
    working: "Deleting…",
    purgeLocked: "Deleting is still locked. Please download the one-page canvas first.",
  },
  draft: {
    heading: "Save your progress",
    locked: "Stopping is locked.",
    otherLock: "Answer at least one question, then download the one-page canvas.",
    failedCheck: "We could not check the download status. Stopping stays locked until it has been checked.",
    ready: "The one-page canvas has been downloaded. You can stop this session when you are ready.",
    help: "Save the documents somewhere you can find them before you stop.",
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

// Shows, for the current revision, whether the server has finished sending each document, why deleting
// is locked, and the delete action only when the server says both are done. "Downloaded" means the
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
  const [last, setLast] = useState<Status | null>(null); // for the rows only, never for deleting
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
      setLast(status);
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
  const known = status ?? last;
  // undefined: being checked. null: the check failed, so the state is unknown.
  const row = (label: string, done: boolean | null | undefined) => (
    <li>
      <span className="dl__name">{label}</span>{" "}
      {done === undefined ? (
        <span className="dl__state">Checking…</span>
      ) : done === null ? (
        <span className="dl__state">
          <span aria-hidden="true">? </span>Could not be checked
        </span>
      ) : done ? (
        <span className="dl__state dl__state--done">
          <span aria-hidden="true">✓ </span>Downloaded
        </span>
      ) : (
        <span className="dl__state">
          <span aria-hidden="true">○ </span>Not downloaded yet
        </span>
      )}
    </li>
  );

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

  if (variant === "draft") {
    // The half-way card is just the download buttons and the way to stop, which unlocks the moment the
    // one-page canvas has been downloaded. Everything else is left out to keep it small.
    return (
      <section className="dl dl--bare card" aria-labelledby="dl-heading">
        <h2 id="dl-heading" className="sr-only">
          {copy.heading}
        </h2>
        {children}
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
        {!eligible && (
          <span id="dl-stop-hint" className="sr-only">
            {copy.lockedHint}
          </span>
        )}
        {state.phase === "failed" && (
          <p role="alert" className="notice">
            {copy.failedCheck}
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

  return (
    <section className="dl card" aria-labelledby="dl-heading">
      <h2 id="dl-heading">{copy.heading}</h2>
      {children}
      <ul className="dl__list">{row("One-page canvas", state.phase === "failed" ? null : known?.canvas_downloaded)}</ul>

      {state.phase === "failed" && (
        <p role="alert" className="notice">
          {copy.failedCheck}
        </p>
      )}
      {status && !status.purge_eligible && (
        <p className="dl__locked">
          <strong>{copy.locked}</strong>{" "}
          {OUTSTANDING[status.lock_reason ?? ""]
            ? `Still to download: ${OUTSTANDING[status.lock_reason ?? ""]}.`
            : copy.otherLock}
        </p>
      )}
      {status?.purge_eligible && (
        <p role="status" className="readback__done">
          {copy.ready}
        </p>
      )}

      <p className="dl__help">
        {copy.help} &ldquo;Downloaded&rdquo; means this app finished sending the file to your browser. It cannot check that
        the file was saved on your device, and nothing is submitted to a backlog. If a download is slow, the status may take
        a moment: choose Refresh status, which does not download the file again.
      </p>

      <div className="dl__actions">
        <button type="button" className="button--secondary" onClick={() => void refresh()} disabled={state.phase === "checking"}>
          Refresh status
        </button>
        {eligible && (
          <button type="button" className="button--danger" onClick={() => setConfirming(true)}>
            {copy.open}
          </button>
        )}
      </div>
      {notice && (
        <p role="alert" className="notice">
          {notice}
        </p>
      )}
      {dialog}
    </section>
  );
}
