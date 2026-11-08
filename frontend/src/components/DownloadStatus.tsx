import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { type DownloadStatus as Status, getDownloadStatus, purgeSession } from "../api/downloads";

// A download can finish just after the first status check, so after each download attempt the
// status is read again a couple of times, then left to the manual "Refresh status" button.
const FOLLOW_UPS_MS = [1500, 4000];

// Fixed copy keyed on the lock reason: nothing from the response is shown.
const OUTSTANDING: Record<string, string> = {
  both_not_downloaded: "the one-page canvas and the appendix",
  canvas_not_downloaded: "the one-page canvas",
  appendix_not_downloaded: "the appendix",
};
const PURGE_MESSAGES: Record<string, string> = {
  purge_locked: "Deleting is still locked. Please download both documents first.",
  revision_conflict: "The canvas changed since it was shown. Please read it again before deleting.",
};
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
  onOutOfDate,
  onPurged,
  onAccessLost,
}: {
  revision: number;
  refreshKey: number; // bumped by the page after each download attempt
  onOutOfDate: () => void;
  onPurged: () => void;
  onAccessLost: () => void;
}) {
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
      setNotice(PURGE_MESSAGES[code] ?? FAILED);
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

  return (
    <section className="dl card" aria-labelledby="dl-heading">
      <h2 id="dl-heading">Your documents</h2>
      <ul className="dl__list">
        {row("One-page canvas", state.phase === "failed" ? null : known?.canvas_downloaded)}
        {row("Appendix", state.phase === "failed" ? null : known?.appendix_downloaded)}
      </ul>

      {state.phase === "failed" && (
        <p role="alert" className="notice">
          We could not check the download status. Deleting stays locked until it has been checked.
        </p>
      )}
      {status && !status.purge_eligible && (
        <p className="dl__locked">
          <strong>Deleting is locked.</strong>{" "}
          {OUTSTANDING[status.lock_reason ?? ""]
            ? `Still to download: ${OUTSTANDING[status.lock_reason ?? ""]}.`
            : "The current canvas must be confirmed and both documents downloaded first."}
        </p>
      )}
      {status?.purge_eligible && (
        <p role="status" className="readback__done">
          Both documents have been downloaded. You can delete this session when you are ready.
        </p>
      )}

      <p className="dl__help">
        Save both documents somewhere you can find them before you delete anything. &ldquo;Downloaded&rdquo; means this app
        finished sending the file to your browser. It cannot check that the file was saved on your device, and nothing is
        submitted to a backlog. If a download is slow, the status may take a moment: choose Refresh status, which does not
        download the file again.
      </p>

      <div className="dl__actions">
        <button type="button" className="button--secondary" onClick={() => void refresh()} disabled={state.phase === "checking"}>
          Refresh status
        </button>
        {status?.purge_eligible && !confirming && (
          <button type="button" className="button--danger" onClick={() => setConfirming(true)}>
            Delete my session…
          </button>
        )}
      </div>

      {status?.purge_eligible && confirming && (
        <div className="dl__confirm" role="group" aria-labelledby="dl-confirm-heading">
          <p id="dl-confirm-heading">
            <strong>Delete this session now?</strong> Your conversation, canvas and documents are removed from the server
            and cannot be recovered.
          </p>
          <div className="dl__actions">
            <button type="button" className="button--danger" onClick={() => void remove()} disabled={deleting}>
              {deleting ? "Deleting…" : "Yes, delete everything"}
            </button>
            <button type="button" className="button--secondary" onClick={() => setConfirming(false)} disabled={deleting}>
              Keep my session
            </button>
          </div>
        </div>
      )}
      {notice && (
        <p role="alert" className="notice">
          {notice}
        </p>
      )}
    </section>
  );
}
