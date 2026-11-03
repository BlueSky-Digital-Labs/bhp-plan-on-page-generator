export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p role="status" className="loading">
      {label}
    </p>
  );
}
