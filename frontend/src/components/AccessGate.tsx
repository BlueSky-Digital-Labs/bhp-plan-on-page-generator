import { type FormEvent, useState } from "react";
import { type SessionInfo, startSession, submitAccessCode } from "../api/access";
import { ApiError } from "../api/client";
import { ErrorNotice } from "./ErrorNotice";

// Fixed copy chosen by error code. Nothing from the server's response body is ever shown.
function messageFor(error: ApiError): string | null {
  if (error.code === "invalid_access_code") {
    return "That access code was not recognised. Check it and try again.";
  }
  if (error.status === 503) {
    return "The service is temporarily unavailable. Please try again shortly.";
  }
  return null; // anything else uses the generic ErrorNotice
}

export function AccessGate({ onSignedIn }: { onSignedIn: (session: SessionInfo) => void }) {
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!code || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await submitAccessCode(code); // valid code -> short-lived grant cookie
      const session = await startSession(); // grant -> private session cookie
      onSignedIn(session);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(null, "client_error", null));
    } finally {
      setCode(""); // never keep the code around once it has been submitted
      setSubmitting(false);
    }
  }

  const friendly = error ? messageFor(error) : null;

  return (
    <section aria-labelledby="access-heading" className="card access">
      <h2 id="access-heading">Enter the access code</h2>
      <p>Enter the code you were given to start your own private canvas.</p>
      <form onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="access-code">Access code</label>
          <input
            id="access-code"
            name="access_code"
            type="password"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            maxLength={128}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            aria-invalid={error?.code === "invalid_access_code" || undefined}
            aria-describedby={error ? "access-error" : undefined}
          />
        </div>
        {error && friendly && (
          <p id="access-error" role="alert" className="access__error">
            {friendly}
          </p>
        )}
        {error && !friendly && <ErrorNotice error={error} />}
        <button type="submit" disabled={!code || submitting}>
          {submitting ? "Checking…" : "Continue"}
        </button>
      </form>
    </section>
  );
}
