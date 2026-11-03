import { type ReactNode, useEffect } from "react";
import { getServiceStatus } from "./api/endpoints";
import { AccessGate } from "./components/AccessGate";
import { CanvasForm } from "./components/CanvasForm";
import { ConversationScreen } from "./components/ConversationScreen";
import { ErrorNotice } from "./components/ErrorNotice";
import { Layout } from "./components/Layout";
import { Loading } from "./components/Loading";
import { StartScreen } from "./components/StartScreen";
import { useAccess } from "./hooks/useAccess";
import { useAccessState } from "./hooks/useAccessState";
import { useRequest } from "./hooks/useRequest";
import { ROUTES, navigate, readNotice, safeRoute, usePath } from "./router";

function CanvasWorkspace() {
  const { state, run } = useRequest(getServiceStatus);

  useEffect(() => {
    void run();
  }, [run]);

  return (
    <>
      <h1>Plan on a Page</h1>
      <p>Complete the AI Opportunity Canvas. Fields marked * are required.</p>

      <CanvasForm />

      <section aria-labelledby="status-heading" className="card status">
        <h2 id="status-heading">Service status</h2>
        {state.phase === "loading" && <Loading label="Checking service…" />}
        {state.phase === "success" && (
          <p data-testid="status-result">
            Service is <strong>{state.data.status}</strong> ({state.data.environment}).
          </p>
        )}
        {state.phase === "error" && <ErrorNotice error={state.error} onRetry={() => void run()} />}
        <button type="button" onClick={() => void run()} disabled={state.phase === "loading"}>
          Check again
        </button>
      </section>
    </>
  );
}

function Redirect({ to, notice }: { to: string; notice?: "access_required" }) {
  useEffect(() => navigate(to, { replace: true, notice }), [to, notice]);
  return <Loading label="Loading…" />;
}

// "/": the access-code form. A browser that already has access skips straight to /start.
function EntryRoute() {
  const { state, recheck } = useAccessState();
  if (state.phase === "checking") return <Loading label="Checking access…" />;
  if (state.phase === "error") return <ErrorNotice error={state.error} onRetry={recheck} />;
  if (state.permitted) return <Redirect to={ROUTES.start} />;
  return (
    <>
      <h1>Plan on a Page</h1>
      {readNotice() === "access_required" && (
        <p role="status" className="notice">
          Your access has expired or has not been entered. Enter the access code to continue.
        </p>
      )}
      <AccessGate onAccepted={() => navigate(ROUTES.start)} />
    </>
  );
}

// "/start": protected. Nothing but a loading state shows until the backend has answered.
function StartRoute() {
  const { state, recheck } = useAccessState();
  if (state.phase === "checking") return <Loading label="Checking access…" />;
  if (state.phase === "error") return <ErrorNotice error={state.error} onRetry={recheck} />;
  if (!state.permitted) return <Redirect to={ROUTES.entry} notice="access_required" />;
  return (
    <StartScreen
      onStarted={(next) => navigate(safeRoute(next))}
      onAccessLost={() => navigate(ROUTES.entry, { replace: true, notice: "access_required" })}
    />
  );
}

// Pages that need a real session, checked by the backend before anything is shown. Losing the
// session (expired, ended, deleted) returns to entry with neutral guidance and drops the page.
function SessionPage({ children }: { children: (onAccessLost: () => void) => ReactNode }) {
  const { state, check, signOut } = useAccess();
  const lost = () => navigate(ROUTES.entry, { replace: true, notice: "access_required" });
  if (state.phase === "checking") return <Loading label="Checking access…" />;
  if (state.phase === "error") return <ErrorNotice error={state.error} onRetry={check} />;
  if (state.phase === "signed_out") return <Redirect to={ROUTES.entry} notice="access_required" />;
  return (
    <>
      <div className="session-bar">
        <span>
          Private session, ends{" "}
          {new Date(state.session.expires_at).toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" })}
        </span>
        <button
          type="button"
          className="button--secondary"
          onClick={() => void signOut().then(() => navigate(ROUTES.entry, { replace: true }))}
        >
          Sign out
        </button>
      </div>
      {children(lost)}
    </>
  );
}

// "/conversation": describe the idea and follow the conversation. History comes from the server.
function ConversationRoute() {
  return <SessionPage>{(lost) => <ConversationScreen onAccessLost={lost} />}</SessionPage>;
}

function CanvasRoute() {
  return <SessionPage>{() => <CanvasWorkspace />}</SessionPage>;
}

export function App() {
  const path = usePath();
  return (
    <Layout>
      {path === ROUTES.entry && <EntryRoute />}
      {path === ROUTES.start && <StartRoute />}
      {path === ROUTES.conversation && <ConversationRoute />}
      {path === ROUTES.canvas && <CanvasRoute />}
      {!Object.values<string>(ROUTES).includes(path) && <Redirect to={ROUTES.entry} />}
    </Layout>
  );
}
