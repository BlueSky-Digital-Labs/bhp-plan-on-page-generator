import { useEffect } from "react";
import { getServiceStatus } from "./api/endpoints";
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
      <p>The application foundation is running. Features will be added here.</p>

      <section aria-labelledby="status-heading" className="card">
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
