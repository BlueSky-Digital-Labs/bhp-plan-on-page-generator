import type { ReactNode } from "react";
import type { CanvasView } from "../api/conversation";
import { OPPORTUNITY_TYPES, PRIORITIES } from "../canvas/fields";

type Fields = CanvasView["fields"];

const text = (fields: Fields, id: string): string => {
  const value = fields[id];
  return Array.isArray(value) ? value.join(", ") : (value ?? "");
};

function Value({ children }: { children: string }) {
  // React renders this as text, so anything the user or model wrote is shown literally.
  return children ? <p className="op-value">{children}</p> : <p className="op-value op-empty">Not provided</p>;
}

function Item({ label, children, plain }: { label: string; children: string; plain?: boolean }) {
  return (
    <div className="op-item">
      <h3 className={plain ? "op-label op-label--plain" : "op-label"}>{label}</h3>
      <Value>{children}</Value>
    </div>
  );
}

function Check({ on, label }: { on: boolean; label: string }) {
  return (
    <li className="op-check">
      <span className={on ? "op-box op-box--on" : "op-box"} aria-hidden="true" />
      {label}
      <span className="op-sr">{on ? " (selected)" : " (not selected)"}</span>
    </li>
  );
}

function Panel({ n, title, className, children }: { n: number; title: string; className: string; children: ReactNode }) {
  return (
    <section className={`op-panel ${className}`} aria-labelledby={`op-h-${n}`}>
      <h2 id={`op-h-${n}`}>
        {n}. {title}
      </h2>
      {children}
    </section>
  );
}

const STEPS: Array<[string, string]> = [
  ["IDENTIFY", "the problem"],
  ["EXPLORE", "AI opportunity"],
  ["DESIGN", "solution concept"],
  ["VALIDATE", "value & feasibility"],
  ["BUILD", "prototype"],
];

// The one-page canvas, laid out and coloured like the AI Opportunity Canvas template. Purely a
// rendering of what the server has recorded: it decides and computes nothing.
export function OnePager({ canvas }: { canvas: CanvasView }) {
  const f = canvas.fields;
  const types = Array.isArray(f.opportunityTypes) ? f.opportunityTypes : [];
  const priority = text(f, "priority");

  return (
    <article className="one-pager" aria-label="AI Opportunity Canvas">
      <header className="op-header">
        <div className="op-heading">
          <h1 className="op-title">
            <span className="op-title__ai">AI</span> OPPORTUNITY <span className="op-title__canvas">CANVAS</span>
          </h1>
          <p className="op-subtitle">
            From Business Problem to Prototype - One Idea, one page, ready to <strong>build</strong>
          </p>
        </div>
        <div className="op-meta">
          <p className="op-bhp">BHP</p>
          <p>
            <span className="op-meta__label">Solution Name:</span> <span className="op-meta__value">{text(f, "solutionName") || "—"}</span>
          </p>
          <p>
            <span className="op-meta__label">Team:</span> <span className="op-meta__value">{text(f, "team") || "—"}</span>
          </p>
          <ul className="op-priority" aria-label="Priority">
            <li className="op-priority__label">Priority:</li>
            {PRIORITIES.map((p) => (
              <Check key={p} on={priority === p} label={p} />
            ))}
          </ul>
        </div>
      </header>

      <div className="op-grid">
        <Panel n={1} title="CURRENT STATE" className="op-panel--orange op-area-1">
          <Item label="Current Process">{text(f, "currentProcess")}</Item>
          <Item label="Pain Points">{text(f, "painPoints")}</Item>
          <Item label="Business Impact">{text(f, "businessImpact")}</Item>
        </Panel>

        <Panel n={2} title="AI OPPORTUNITY" className="op-panel--purple op-area-2">
          <ul className="op-types" aria-label="Opportunity type">
            {OPPORTUNITY_TYPES.map((t) => (
              <Check key={t} on={types.includes(t)} label={t} />
            ))}
          </ul>
          <Item label="Future State Vision">{text(f, "futureState")}</Item>
        </Panel>

        <Panel n={3} title="SOLUTION CONCEPT" className="op-panel--blue op-area-3">
          <Item label="What do we want to achieve through AI?">{text(f, "achieve")}</Item>
          <Item label="What does success look like?">{text(f, "success")}</Item>
        </Panel>

        <Panel n={4} title="VALUE & SUCCESS" className="op-panel--green op-area-4">
          <Item label="Expected Value/KPIs" plain>
            {text(f, "kpis")}
          </Item>
        </Panel>

        <Panel n={5} title="KEY ASSUMPTIONS & QUESTIONS" className="op-panel--orange op-area-5">
          <Item label="Who will use this solution?" plain>{text(f, "users")}</Item>
          <Item label="What decision will it improve?" plain>{text(f, "decision")}</Item>
          <Item label="What data is needed?" plain>{text(f, "data")}</Item>
          <Item label="What systems must connect?" plain>{text(f, "systems")}</Item>
          <Item label="Key risks and dependencies?" plain>{text(f, "risks")}</Item>
        </Panel>
      </div>

      <footer className="op-footer">
        <p className="op-tagline">
          One idea. One page. Ready to <em>build</em>.
        </p>
        <ol className="op-steps">
          {STEPS.map(([step, what]) => (
            <li key={step}>
              <strong>{step}</strong>
              <span>{what}</span>
            </li>
          ))}
        </ol>
        <p className="op-badge">
          <strong>AI LAB</strong>
          <span>SOLUTION ONE PAGER</span>
        </p>
      </footer>
    </article>
  );
}
