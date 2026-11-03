import { useEffect } from "react";
import { getServiceStatus } from "./api/endpoints";
import { AccessGate } from "./components/AccessGate";
import { CanvasForm } from "./components/CanvasForm";
import { ErrorNotice } from "./components/ErrorNotice";
import { Layout } from "./components/Layout";
import { Loading } from "./components/Loading";
import { useAccess } from "./hooks/useAccess";
import { useRequest } from "./hooks/useRequest";

function Workspace({ expiresAt, onSignOut }: { expiresAt: string; onSignOut: () => void }) {
  const { state, run } = useRequest(getServiceStatus);

  useEffect(() => {
    void run();
  }, [run]);

  return (
    <>
      <div className="session-bar">
        <span>
          Private session, ends {new Date(expiresAt).toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" })}
        </span>
        <button type="button" className="button--secondary" onClick={onSignOut}>
          Sign out
        </button>
      </div>
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

export function App() {
  const { state, check, signedIn, signOut } = useAccess();

  return (
    <Layout>
      {state.phase === "checking" && <Loading label="Checking access…" />}
      {state.phase === "error" && <ErrorNotice error={state.error} onRetry={check} />}
      {state.phase === "signed_out" && (
        <>
          <h1>Plan on a Page</h1>
          <AccessGate onSignedIn={signedIn} />
        </>
      )}
      {state.phase === "signed_in" && (
        <Workspace expiresAt={state.session.expires_at} onSignOut={() => void signOut()} />
      )}
    </Layout>
  );
}
