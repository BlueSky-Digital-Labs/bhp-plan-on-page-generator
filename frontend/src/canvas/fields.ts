// Single source of truth for the AI Opportunity Canvas (proposal Appendix A).
// Required for export: every field except Business Impact and Expected Value/KPIs,
// plus at least one opportunity type.

// The user's own choice, and only ever one of these three (matches backend/app/interview/canvas.py).
export const PRIORITIES = ["Must-have", "Nice-to-have", "Exploratory"] as const;
export type Priority = (typeof PRIORITIES)[number];

export const OPPORTUNITY_TYPES = [
  "Find Information",
  "Generate Reports",
  "Predict Issues",
  "Recommend Actions",
  "Analyse Data",
  "Automate Workflows",
] as const;
export type OpportunityType = (typeof OPPORTUNITY_TYPES)[number];

export interface TextField {
  id: string;
  label: string;
  required: boolean;
}

export interface Section {
  id: string;
  number: number;
  title: string;
  fields: TextField[];
}

export const HEADER_FIELDS: TextField[] = [
  { id: "solutionName", label: "Solution Name", required: true },
  { id: "team", label: "Team", required: true },
];

export const SECTIONS: Section[] = [
  {
    id: "current-state",
    number: 1,
    title: "Current State",
    fields: [
      { id: "currentProcess", label: "Current Process", required: true },
      { id: "painPoints", label: "Pain Points", required: true },
      { id: "businessImpact", label: "Business Impact", required: false },
    ],
  },
  {
    id: "ai-opportunity",
    number: 2,
    title: "AI Opportunity",
    fields: [{ id: "futureState", label: "Future State Vision", required: true }],
  },
  {
    id: "solution-concept",
    number: 3,
    title: "Solution Concept",
    fields: [
      { id: "achieve", label: "What do we want to achieve through AI?", required: true },
      { id: "success", label: "What does success look like?", required: true },
    ],
  },
  {
    id: "value-success",
    number: 4,
    title: "Value & Success",
    fields: [{ id: "kpis", label: "Expected Value/KPIs", required: false }],
  },
  {
    id: "assumptions",
    number: 5,
    title: "Key Assumptions & Questions",
    fields: [
      { id: "users", label: "Who will use this solution?", required: true },
      { id: "decision", label: "What decision will it improve?", required: true },
      { id: "data", label: "What data is needed?", required: true },
      { id: "systems", label: "What systems must connect?", required: true },
      { id: "risks", label: "Key risks and dependencies?", required: true },
    ],
  },
];

export interface CanvasValues {
  text: Record<string, string>;
  priority: Priority | "";
  opportunityTypes: OpportunityType[];
}

export const emptyCanvas = (): CanvasValues => ({ text: {}, priority: "", opportunityTypes: [] });

/** Labels of the required fields that are still empty (whitespace-only counts as empty). */
export function missingRequired(values: CanvasValues): string[] {
  const missing = [...HEADER_FIELDS, ...SECTIONS.flatMap((s) => s.fields)]
    .filter((f) => f.required && !values.text[f.id]?.trim())
    .map((f) => f.label);
  if (!values.priority) missing.push("Priority");
  if (values.opportunityTypes.length === 0) missing.push("Opportunity type");
  return missing;
}
