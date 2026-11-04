import type { CanvasView } from "../api/conversation";
import { HEADER_FIELDS, SECTIONS } from "../canvas/fields";

const EMPTY: CanvasView = { fields: {}, missing_required: [], ready_for_review: false };

type Row = { id: string; label: string; required: boolean };

// Every canvas field in the order of the template, labels from the one canvas definition.
const ROWS: Row[] = [
  ...HEADER_FIELDS,
  { id: "priority", label: "Priority", required: true },
  { id: "opportunityTypes", label: "Opportunity type", required: true },
  ...SECTIONS.flatMap((s) => s.fields),
];

function show(value: string | string[] | undefined): string | null {
  if (Array.isArray(value)) return value.length ? value.join(", ") : null;
  return value ? value : null;
}

// Shows what the server has recorded in the draft. It only ever displays; the validator and the
// interviewer on the server decide what is complete. Values render as text (React escapes them).
export function CanvasPanel({
  canvas = EMPTY,
  onGenerate,
}: {
  canvas?: CanvasView;
  onGenerate?: () => void;
}) {
  const remaining = canvas.missing_required.length;
  return (
    <section aria-labelledby="canvas-heading" className="canvas-panel card">
      <h2 id="canvas-heading">Your canvas so far</h2>
      <p data-testid="canvas-progress" role="status">
        {canvas.ready_for_review
          ? "Ready for review: every required answer is in."
          : remaining > 0
            ? `${remaining} required answer${remaining === 1 ? "" : "s"} still to go.`
            : "Nearly there: a few last questions."}
      </p>
      {canvas.ready_for_review && onGenerate && (
        <button type="button" className="canvas-panel__generate" onClick={onGenerate}>
          Generate canvas
        </button>
      )}
      <dl>
        {ROWS.map((row) => {
          const value = show(canvas.fields[row.id]);
          return (
            <div key={row.id} className="canvas-panel__row" data-testid={`canvas-${row.id}`}>
              <dt>
                {row.label}
                {!row.required && <span className="canvas-panel__optional"> (optional)</span>}
              </dt>
              <dd className={value ? undefined : "canvas-panel__empty"}>{value ?? "Not answered yet"}</dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}
