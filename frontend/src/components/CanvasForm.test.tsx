import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { HEADER_FIELDS, OPPORTUNITY_TYPES, PRIORITIES, SECTIONS } from "../canvas/fields";
import { CanvasForm } from "./CanvasForm";

const progress = () => screen.getByTestId("canvas-progress");
const requiredText = [...HEADER_FIELDS, ...SECTIONS.flatMap((s) => s.fields)].filter((f) => f.required);

describe("CanvasForm", () => {
  it("renders the five panels with every field, type and priority", () => {
    render(<CanvasForm />);
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(
      SECTIONS.map((s) => `${s.number}. ${s.title}`),
    );
    for (const f of [...HEADER_FIELDS, ...SECTIONS.flatMap((s) => s.fields)]) {
      expect(screen.getByLabelText(f.label)).toBeInTheDocument();
    }
    for (const t of OPPORTUNITY_TYPES) expect(screen.getByRole("checkbox", { name: t })).toBeInTheDocument();
    for (const p of PRIORITIES) expect(screen.getByRole("radio", { name: p })).toBeInTheDocument();
  });

  it("marks required fields and leaves Business Impact and KPIs optional", () => {
    render(<CanvasForm />);
    expect(screen.getByLabelText("Pain Points")).toBeRequired();
    expect(screen.getByLabelText("Business Impact")).not.toBeRequired();
    expect(screen.getByLabelText("Expected Value/KPIs")).not.toBeRequired();
  });

  it("counts down required fields as the user fills them in", async () => {
    const user = userEvent.setup();
    render(<CanvasForm />);
    expect(progress()).toHaveTextContent(`${requiredText.length + 2} required fields remaining.`);

    await user.type(screen.getByLabelText("Solution Name"), "ROC assistant");
    await user.click(screen.getByRole("radio", { name: "Exploratory" }));
    await user.click(screen.getByRole("checkbox", { name: "Generate Reports" }));
    expect(progress()).toHaveTextContent(`${requiredText.length - 1} required fields remaining.`);

    await user.click(screen.getByRole("checkbox", { name: "Generate Reports" }));
    expect(progress()).toHaveTextContent(`${requiredText.length} required fields remaining.`);
  });

  it("reports completion without the optional fields", async () => {
    const user = userEvent.setup();
    render(<CanvasForm />);
    for (const f of requiredText) await user.type(screen.getByLabelText(f.label), "x");
    await user.click(screen.getByRole("radio", { name: "Nice-to-have" }));
    await user.click(screen.getByRole("checkbox", { name: "Find Information" }));
    expect(progress()).toHaveTextContent("All required fields are complete.");
  });
});
