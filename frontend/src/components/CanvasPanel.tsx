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

const REQUIRED_TOTAL = ROWS.filter((row) => row.required).length;

function show(value: string | string[] | undefined): string | null {
  if (Array.isArray(value)) return value.length ? value.join(", ") : null;
  return value ? value : null;
}

// Shows what the server has recorded in the draft. It only ever displays; the validator and the
// interviewer on the server decide what is complete. Values render as text (React escapes them).
export function CanvasPanel({ canvas = EMPTY }: { canvas?: CanvasView }) {
  const remaining = canvas.missing_required.length;
  const answered = Math.min(REQUIRED_TOTAL, Math.max(0, REQUIRED_TOTAL - remaining));
  const percent = canvas.ready_for_review ? 100 : Math.round((answered / REQUIRED_TOTAL) * 100);
  return (
    <section aria-labelledby="canvas-heading" className="canvas-panel card">
      <h2 id="canvas-heading">Your canvas so far</h2>
      {/* Decorative: the sentence below says the same in words. */}
      <div className="canvas-panel__bar" aria-hidden="true">
        <span style={{ width: `${percent}%` }} />
      </div>
      <p data-testid="canvas-progress" role="status">
        {canvas.ready_for_review
          ? "Ready for review: every required answer is in."
          : remaining > 0
            ? `${remaining} required answer${remaining === 1 ? "" : "s"} still to go.`
            : "Nearly there: a few last questions."}
      </p>
      {/* A small box that scrolls. Focusable and labelled so keyboard users can scroll it too. */}
      <div className="canvas-panel__scroll" role="region" aria-label="Canvas fields so far" tabIndex={0}>
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
      </div>
    </section>
  );
}
