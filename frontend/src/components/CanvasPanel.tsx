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

// Shows which fields the server has recorded in the draft, as a tick each (never the answers
// themselves). It only ever displays; the validator and the interviewer on the server decide what is
// complete.
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
      {/* A checklist, not the answers: a tick once the field has an answer. It is a small box that
          scrolls, focusable and labelled so keyboard users can scroll it too. */}
      <div className="canvas-panel__scroll" role="region" aria-label="Canvas fields so far" tabIndex={0}>
        <ul className="canvas-panel__list">
          {ROWS.map((row) => {
            const done = show(canvas.fields[row.id]) !== null;
            return (
              <li
                key={row.id}
                className={done ? "canvas-panel__row canvas-panel__row--done" : "canvas-panel__row"}
                data-testid={`canvas-${row.id}`}
              >
                <span className="canvas-panel__tick" aria-hidden="true">
                  {done ? "✓" : ""}
                </span>
                <span className="canvas-panel__label">
                  {row.label}
                  {!row.required && <span className="canvas-panel__optional"> (optional)</span>}
                </span>
                <span className="sr-only">{done ? "Answered" : "Not answered yet"}</span>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
