import { describe, expect, it } from "vitest";
import { HEADER_FIELDS, SECTIONS, emptyCanvas, missingRequired, type CanvasValues } from "./fields";

const allFields = [...HEADER_FIELDS, ...SECTIONS.flatMap((s) => s.fields)];

function completeCanvas(): CanvasValues {
  const text = Object.fromEntries(allFields.filter((f) => f.required).map((f) => [f.id, "answer"]));
  return { text, priority: "Must-have", opportunityTypes: ["Analyse Data"] };
}

describe("missingRequired", () => {
  it("lists every required field on an empty canvas", () => {
    const required = allFields.filter((f) => f.required).length;
    // + Priority + Opportunity type
    expect(missingRequired(emptyCanvas())).toHaveLength(required + 2);
  });

  it("is empty once every required field is filled", () => {
    expect(missingRequired(completeCanvas())).toEqual([]);
  });

  it("does not require Business Impact or Expected Value/KPIs", () => {
    const optional = allFields.filter((f) => !f.required).map((f) => f.label);
    expect(optional).toEqual(["Business Impact", "Expected Value/KPIs"]);
    expect(missingRequired(completeCanvas())).not.toEqual(expect.arrayContaining(optional));
  });

  it("treats whitespace-only text as empty", () => {
    const values = completeCanvas();
    values.text.painPoints = "   \n ";
    expect(missingRequired(values)).toEqual(["Pain Points"]);
  });

  it("requires a priority and at least one opportunity type", () => {
    const values = { ...completeCanvas(), priority: "" as const, opportunityTypes: [] };
    expect(missingRequired(values)).toEqual(["Priority", "Opportunity type"]);
  });
});
