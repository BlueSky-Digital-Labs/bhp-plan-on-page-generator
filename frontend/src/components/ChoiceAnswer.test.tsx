import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChoiceAnswer } from "./ChoiceAnswer";

const ONE = { multiple: false, options: ["Must-have", "Nice-to-have", "Exploratory"] };
const MANY = { multiple: true, options: ["Find Information", "Generate Reports", "Predict Issues"] };

function setup(choices = ONE, props: { disabled?: boolean; sending?: boolean } = {}) {
  const onSend = vi.fn();
  const onTypeOwn = vi.fn();
  render(<ChoiceAnswer choices={choices} disabled={props.disabled ?? false} sending={props.sending ?? false} onSend={onSend} onTypeOwn={onTypeOwn} />);
  return { onSend, onTypeOwn };
}
const send = () => screen.getByRole("button", { name: /^(Send|Sending…)$/ });

describe("ChoiceAnswer", () => {
  it("offers exactly the options it is given, to pick one, and nothing is selected to begin with", () => {
    setup();
    expect(screen.getByRole("group", { name: "Choose one" })).toBeInTheDocument();
    expect(screen.getAllByRole("radio").map((r) => (r as HTMLInputElement).value)).toEqual(ONE.options);
    expect(screen.getAllByRole("radio").every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    expect(send()).toBeDisabled(); // nothing to send until something is picked
  });

  it("sends the chosen option's own wording", async () => {
    const { onSend } = setup();
    await userEvent.click(screen.getByLabelText("Nice-to-have"));
    await userEvent.click(send());
    expect(onSend).toHaveBeenCalledExactlyOnceWith("Nice-to-have");
  });

  it("lets the choice be changed before sending, and only one can be held", async () => {
    const { onSend } = setup();
    await userEvent.click(screen.getByLabelText("Must-have"));
    await userEvent.click(screen.getByLabelText("Exploratory"));
    expect(screen.getByLabelText("Must-have")).not.toBeChecked();
    await userEvent.click(send());
    expect(onSend).toHaveBeenCalledExactlyOnceWith("Exploratory");
  });

  it("works from the keyboard: arrow to an option, then Tab to Send and press Enter", async () => {
    const { onSend } = setup();
    await userEvent.tab();
    await userEvent.keyboard("{ArrowDown}"); // moves the radio group to the second option and selects it
    expect(screen.getByLabelText("Nice-to-have")).toBeChecked();
    await userEvent.tab();
    await userEvent.keyboard("{Enter}");
    expect(onSend).toHaveBeenCalledExactlyOnceWith("Nice-to-have");
  });

  it("lets several be picked when the answer can be more than one, and sends them in the list's order", async () => {
    const { onSend } = setup(MANY);
    expect(screen.getByRole("group", { name: "Choose all that fit" })).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Predict Issues"));
    await userEvent.click(screen.getByLabelText("Find Information"));
    await userEvent.click(send());
    expect(onSend).toHaveBeenCalledExactlyOnceWith("Find Information, Predict Issues");
  });

  it("lets a pick be taken back, and Send waits again when none is left", async () => {
    setup(MANY);
    await userEvent.click(screen.getByLabelText("Find Information"));
    expect(send()).toBeEnabled();
    await userEvent.click(screen.getByLabelText("Find Information"));
    expect(send()).toBeDisabled();
  });

  it("offers typing instead, without sending anything", async () => {
    const { onSend, onTypeOwn } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Type my own answer instead" }));
    expect(onTypeOwn).toHaveBeenCalledOnce();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("is switched off while an answer is being sent or a reply is coming", async () => {
    const { onSend } = setup(ONE, { disabled: true, sending: true });
    screen.getAllByRole("radio").forEach((radio) => expect(radio).toBeDisabled()); // via the disabled fieldset
    expect(send()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Type my own answer instead" })).toBeDisabled();
    expect(send()).toHaveTextContent("Sending…");
    expect(onSend).not.toHaveBeenCalled();
  });
});
