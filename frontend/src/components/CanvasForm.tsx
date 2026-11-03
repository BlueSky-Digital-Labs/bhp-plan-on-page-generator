import { useState } from "react";
import {
  HEADER_FIELDS,
  OPPORTUNITY_TYPES,
  PRIORITIES,
  SECTIONS,
  emptyCanvas,
  missingRequired,
  type CanvasValues,
  type OpportunityType,
  type Priority,
  type TextField,
} from "../canvas/fields";

export function CanvasForm() {
  // Held in memory only: nothing is stored in the browser or sent anywhere.
  const [values, setValues] = useState<CanvasValues>(emptyCanvas);
  const remaining = missingRequired(values).length;

  const setText = (id: string, value: string) =>
    setValues((v) => ({ ...v, text: { ...v.text, [id]: value } }));

  const toggleType = (type: OpportunityType) =>
    setValues((v) => ({
      ...v,
      opportunityTypes: v.opportunityTypes.includes(type)
        ? v.opportunityTypes.filter((t) => t !== type)
        : [...v.opportunityTypes, type],
    }));

  const field = (f: TextField, multiline: boolean) => {
    const id = `field-${f.id}`;
    const props = {
      id,
      required: f.required,
      value: values.text[f.id] ?? "",
      onChange: (e: { target: { value: string } }) => setText(f.id, e.target.value),
    };
    return (
      <div className="field" key={f.id}>
        <label htmlFor={id} className={f.required ? "required" : undefined}>
          {f.label}
        </label>
        {multiline ? <textarea rows={4} {...props} /> : <input type="text" {...props} />}
      </div>
    );
  };

  return (
    <form className="canvas" onSubmit={(e) => e.preventDefault()} aria-label="AI Opportunity Canvas">
      <div className="canvas__header">
        {HEADER_FIELDS.map((f) => field(f, false))}
        <fieldset className="field">
          <legend className="required">Priority</legend>
          <div className="choices">
            {PRIORITIES.map((p: Priority) => (
              <label key={p}>
                <input
                  type="radio"
                  name="priority"
                  value={p}
                  checked={values.priority === p}
                  onChange={() => setValues((v) => ({ ...v, priority: p }))}
                />{" "}
                {p}
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      <p className="canvas__progress" data-testid="canvas-progress" aria-live="polite">
        {remaining === 0
          ? "All required fields are complete."
          : `${remaining} required ${remaining === 1 ? "field" : "fields"} remaining.`}
      </p>

      <div className="canvas__panels">
        {SECTIONS.map((s) => (
          <section key={s.id} className={`panel panel--${s.number}`} aria-labelledby={`panel-${s.id}`}>
            <h2 id={`panel-${s.id}`}>
              {s.number}. {s.title}
            </h2>
            {s.id === "ai-opportunity" && (
              <fieldset className="field">
                <legend className="required">Opportunity type (choose at least one)</legend>
                <div className="choices">
                  {OPPORTUNITY_TYPES.map((t) => (
                    <label key={t}>
                      <input
                        type="checkbox"
                        checked={values.opportunityTypes.includes(t)}
                        onChange={() => toggleType(t)}
                      />{" "}
                      {t}
                    </label>
                  ))}
                </div>
              </fieldset>
            )}
            {s.fields.map((f) => field(f, true))}
          </section>
        ))}
      </div>
    </form>
  );
}
