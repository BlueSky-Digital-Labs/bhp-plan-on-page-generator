import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import type { CanvasView } from "../api/conversation";
import { OnePager } from "./OnePager";

const FULL: CanvasView = {
  ready_for_review: true,
  missing_required: [],
  fields: {
    solutionName: "Shift Notes Assistant",
    team: "Operations",
    priority: "High",
    opportunityTypes: ["Find Information", "Generate Reports"],
    currentProcess: "Copy notes between spreadsheets",
    painPoints: "Takes hours",
    futureState: "One shared summary",
    achieve: "Faster handover",
    success: "Handover takes minutes",
    kpis: "Hours saved",
    users: "Shift supervisors",
    decision: "What to escalate",
    data: "Shift logs",
    systems: "Rostering system",
    risks: "Sensitive details",
  },
};

// Vitest stubs CSS imports to empty text, so read the real stylesheet from disk.
const styles = readFileSync("src/styles.css", "utf8"); // tests run from the frontend folder

const panel = (name: RegExp) => within(screen.getByRole("region", { name }));

describe("OnePager", () => {
  it("lays the canvas out as the five numbered sections of the template", () => {
    render(<OnePager canvas={FULL} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("AI OPPORTUNITY CANVAS");
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual([
      "1. CURRENT STATE",
      "2. AI OPPORTUNITY",
      "3. SOLUTION CONCEPT",
      "4. VALUE & SUCCESS",
      "5. KEY ASSUMPTIONS & QUESTIONS",
    ]);
  });

  it("puts every saved answer in its own section under the template's wording", () => {
    render(<OnePager canvas={FULL} />);
    expect(panel(/CURRENT STATE/).getByText("Copy notes between spreadsheets")).toBeInTheDocument();
    expect(panel(/CURRENT STATE/).getByText("Takes hours")).toBeInTheDocument();
    expect(panel(/AI OPPORTUNITY/).getByText("One shared summary")).toBeInTheDocument();
    expect(panel(/SOLUTION CONCEPT/).getByText("Faster handover")).toBeInTheDocument();
    expect(panel(/SOLUTION CONCEPT/).getByText("Handover takes minutes")).toBeInTheDocument();
    expect(panel(/VALUE/).getByText("Hours saved")).toBeInTheDocument();
    for (const [label, value] of [
      ["Who will use this solution?", "Shift supervisors"],
      ["What decision will it improve?", "What to escalate"],
      ["What data is needed?", "Shift logs"],
      ["What systems must connect?", "Rostering system"],
      ["Key risks and dependencies?", "Sensitive details"],
    ]) {
      expect(panel(/KEY ASSUMPTIONS/).getByText(label)).toBeInTheDocument();
      expect(panel(/KEY ASSUMPTIONS/).getByText(value)).toBeInTheDocument();
    }
    expect(screen.getByText("Shift Notes Assistant")).toBeInTheDocument();
    expect(screen.getByText("Operations")).toBeInTheDocument();
  });

  it("ticks exactly the chosen priority and opportunity types, and says so in words", () => {
    render(<OnePager canvas={FULL} />);
    const selected = (listName: string) =>
      within(screen.getByRole("list", { name: listName }))
        .getAllByRole("listitem")
        .filter((li) => li.textContent?.includes("(selected)"))
        .map((li) => li.textContent?.replace(" (selected)", ""));
    expect(selected("Priority")).toEqual(["High"]);
    expect(selected("Opportunity type")).toEqual(["Find Information", "Generate Reports"]);
    expect(within(screen.getByRole("list", { name: "Opportunity type" })).getAllByRole("listitem")).toHaveLength(6);
  });

  it("shows an unanswered optional field as 'Not provided' rather than inventing anything", () => {
    render(<OnePager canvas={{ ...FULL, fields: { ...FULL.fields, businessImpact: undefined as never } }} />);
    expect(panel(/CURRENT STATE/).getByText("Not provided")).toBeInTheDocument();
  });

  it("renders anything the user or model wrote as inert text", () => {
    const hostile = '<img src=x onerror="alert(1)"> <script>alert(2)</script>';
    const { container } = render(
      <OnePager canvas={{ ...FULL, fields: { ...FULL.fields, painPoints: hostile, solutionName: hostile } }} />,
    );
    expect(screen.getAllByText(hostile).length).toBeGreaterThan(0);
    expect(container.querySelector("img, script")).toBeNull();
  });

  it("uses the exact colours sampled from the template image", () => {
    const tokens: Record<string, string> = {
      "--op-orange": "#f37021",
      "--op-purple": "#7b61ff",
      "--op-blue": "#02b5e2",
      "--op-green": "#00c48c",
      "--op-bg": "#1e1e1e",
      "--op-panel": "#242424",
      "--op-footer": "#070f19",
    };
    for (const [name, hex] of Object.entries(tokens)) expect(styles).toContain(`${name}: ${hex}`);
    // The template form's section panels use the same colours.
    for (const rule of [".panel--1 { --panel: #f37021", ".panel--2 { --panel: #7b61ff", ".panel--3 { --panel: #02b5e2", ".panel--4 { --panel: #00c48c", ".panel--5 { --panel: #f37021"]) {
      expect(styles).toContain(rule);
    }
  });
});
