import { useState } from "react";
import { startSession } from "../api/access";
import { ApiError } from "../api/client";
import { ErrorNotice } from "./ErrorNotice";

export function StartScreen({
  onStarted,
  onAccessLost,
}: {
  onStarted: (next: string) => void;
  onAccessLost: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function start() {
    if (pending) return; // duplicate clicks are ignored while a request is in flight
    setPending(true);
    setError(null);
    try {
      const session = await startSession();
      onStarted(session.next);
    } catch (caught) {
      const apiError = caught instanceof ApiError ? caught : new ApiError(null, "client_error", null);
      if (apiError.status === 401) {
        onAccessLost(); // the grant expired or was used: back to entry, with neutral guidance
        return;
      }
      setError(apiError);
      setPending(false); // restore the action so the user can retry
    }
  }

  return (
    <>
      <h1>Start a new canvas</h1>

      <section className="card start" aria-labelledby="how-heading">
        <h2 id="how-heading">What you will do</h2>
        <ol>
          <li>Describe your idea.</li>
          <li>Answer a few questions about it.</li>
          <li>Choose your own priority.</li>
          <li>Review the canvas.</li>
          <li>Save both the one-page canvas and the appendix.</li>
        </ol>
      </section>

      <section className="card start" aria-labelledby="save-heading">
        <h2 id="save-heading">Saving your work</h2>
        <p>
          You save the canvas and the appendix yourself, to your own device. Downloading them does{" "}
          <strong>not</strong> submit anything to a backlog or to anyone else.
        </p>
        <p>
          We cannot confirm that a download has been saved on your device, so check that both files
          opened or saved correctly before you leave.
        </p>
      </section>

      <section className="card start" aria-labelledby="data-heading">
        <h2 id="data-heading">How your idea is handled</h2>
        <ul>
          <li>Your idea is processed temporarily, on Australian-hosted infrastructure.</li>
          <li>
            After you have downloaded the one-page canvas, you can choose to purge your session straight
            away.
          </li>
          <li>
            Whether or not you do, everything is deleted automatically if nothing happens for 15 minutes, and in
            any case 24 hours after you start, even if you close the browser. A session that has ended cannot be
            recovered, so download what you need to keep.
          </li>
        </ul>
        <p className="start__note">
          This describes how the temporary service is designed to work. It is not a statement that
          data-handling approval has been granted.
        </p>
      </section>

      <p>Starting a new canvas always creates a separate session. It never resumes or replaces an earlier one.</p>

      {error && <ErrorNotice error={error} />}
      <button type="button" onClick={() => void start()} disabled={pending}>
        {pending ? "Starting…" : "Start a new canvas"}
      </button>
    </>
  );
}
