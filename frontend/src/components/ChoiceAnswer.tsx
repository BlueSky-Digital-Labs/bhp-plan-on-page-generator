import { useState } from "react";
import type { Choices } from "../api/conversation";

// For a question whose answer is one (or several) of a fixed list: pick, then send. Picking sends the
// option's own wording as the answer, exactly as if the user had typed it, so the server treats it
// like any other answer. Typing something different stays one click away.
export function ChoiceAnswer({
  choices,
  disabled,
  sending,
  onSend,
  onTypeOwn,
}: {
  choices: Choices;
  disabled: boolean;
  sending: boolean;
  onSend: (text: string) => void;
  onTypeOwn: () => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const { multiple, options } = choices;

  const toggle = (option: string) =>
    setPicked((now) => (multiple ? (now.includes(option) ? now.filter((o) => o !== option) : [...now, option]) : [option]));

  const submit = (event: { preventDefault(): void }) => {
    event.preventDefault();
    if (disabled || picked.length === 0) return;
    onSend(options.filter((o) => picked.includes(o)).join(", ")); // in the list's order, not click order
  };

  return (
    <form className="choices" onSubmit={submit}>
      <fieldset disabled={disabled}>
        <legend>{multiple ? "Choose all that fit" : "Choose one"}</legend>
        <ul className="choices__list">
          {options.map((option) => {
            const id = `choice-${option.replace(/[^A-Za-z0-9]+/g, "-").toLowerCase()}`;
            return (
              <li key={option}>
                <input
                  id={id}
                  type={multiple ? "checkbox" : "radio"}
                  name="choice"
                  value={option}
                  checked={picked.includes(option)}
                  onChange={() => toggle(option)}
                />
                <label htmlFor={id}>{option}</label>
              </li>
            );
          })}
        </ul>
      </fieldset>
      <div className="actions">
        <button type="submit" disabled={disabled || picked.length === 0}>
          {sending ? "Sending…" : "Send"}
        </button>
        <button type="button" className="button--secondary" onClick={onTypeOwn} disabled={disabled}>
          Type my own answer instead
        </button>
      </div>
    </form>
  );
}
