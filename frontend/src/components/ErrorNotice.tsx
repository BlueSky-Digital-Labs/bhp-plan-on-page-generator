import type { ApiError } from "../api/client";

// The only text shown for a failure. It is fixed copy plus a non-content reference the
// user can quote to support; no server message, status text, stack or submitted text.
export function ErrorNotice({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div role="alert" className="error-notice">
      <p className="error-notice__message">Something went wrong. Please try again.</p>
      {error.correlationId && (
        <p className="error-notice__reference">
          Reference: <code>{error.correlationId}</code>
        </p>
      )}
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
