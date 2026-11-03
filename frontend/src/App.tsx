import { useEffect } from "react";
import { getServiceStatus } from "./api/endpoints";
import { CanvasForm } from "./components/CanvasForm";
import { ErrorNotice } from "./components/ErrorNotice";
import { Layout } from "./components/Layout";
import { Loading } from "./components/Loading";
import { useRequest } from "./hooks/useRequest";

export function App() {
  const { state, run } = useRequest(getServiceStatus);

  useEffect(() => {
    void run();
  }, [run]);

  return (
    <Layout>
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
    </Layout>
  );
}
