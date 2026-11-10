import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CanvasView } from "../api/conversation";
import { missingRequired, emptyCanvas } from "../canvas/fields";
import { CanvasPanel } from "./CanvasPanel";

const view = (over: Partial<CanvasView> = {}): CanvasView => ({
  fields: {},
  missing_required: [],
  ready_for_review: false,
  ...over,
});

const row = (id: string) => within(screen.getByTestId(`canvas-${id}`));
const labels = () => screen.getAllByRole("listitem").map((li) => li.querySelector(".canvas-panel__label")?.textContent);

describe("CanvasPanel", () => {
  it("lists every canvas field in template order, unanswered by default", () => {
    render(<CanvasPanel canvas={view({ missing_required: Array(14).fill("x") })} />);
    expect(labels()).toEqual([
      "Solution Name",
      "Team",
      "Priority",
      "Opportunity type",
      "Current Process",
      "Pain Points",
      "Business Impact (optional)",
      "Future State Vision",
      "What do we want to achieve through AI?",
      "What does success look like?",
      "Expected Value/KPIs (optional)",
      "Who will use this solution?",
      "What decision will it improve?",
      "What data is needed?",
      "What systems must connect?",
      "Key risks and dependencies?",
    ]);
    expect(screen.getAllByText("Not answered yet")).toHaveLength(16);
    expect(screen.queryByText("Answered")).not.toBeInTheDocument();
    expect(screen.getByTestId("canvas-progress")).toHaveTextContent("14 required answers still to go.");
  });

  it("covers exactly the fields the shared canvas definition requires", () => {
    // 14 required answers on the server must equal what the form's own validator counts.
    expect(missingRequired(emptyCanvas())).toHaveLength(14);
  });

  it("ticks a field once it has an answer, and never shows the answer itself", () => {
    render(
      <CanvasPanel
        canvas={view({
          fields: { solutionName: "Shift Notes Assistant", opportunityTypes: ["Find Information", "Generate Reports"], priority: "Must-have" },
          missing_required: Array(11).fill("x"),
        })}
      />,
    );
    for (const id of ["solutionName", "opportunityTypes", "priority"]) {
      expect(row(id).getByText("Answered")).toBeInTheDocument();
      expect(screen.getByTestId(`canvas-${id}`)).toHaveTextContent("✓");
    }
    expect(row("team").getByText("Not answered yet")).toBeInTheDocument();
    expect(screen.getAllByText("Answered")).toHaveLength(3);
    expect(screen.queryByText(/Shift Notes Assistant|Must-have|Find Information/)).not.toBeInTheDocument();
    expect(screen.getByTestId("canvas-progress")).toHaveTextContent("11 required answers still to go.");
  });

  it("says when the canvas is ready for review, and uses the singular for one remaining answer", () => {
    const { rerender } = render(<CanvasPanel canvas={view({ missing_required: ["team"] })} />);
    expect(screen.getByTestId("canvas-progress")).toHaveTextContent("1 required answer still to go.");
    rerender(<CanvasPanel canvas={view({ ready_for_review: true })} />);
    expect(screen.getByTestId("canvas-progress")).toHaveTextContent("Ready for review");
  });

  it("does not render what was saved, so markup in an answer can never reach the page", () => {
    const hostile = '<img src=x onerror="alert(1)"> <script>alert(2)</script>';
    const { container } = render(<CanvasPanel canvas={view({ fields: { painPoints: hostile } })} />);
    expect(row("painPoints").getByText("Answered")).toBeInTheDocument();
    expect(container.textContent).not.toContain("alert(");
    expect(container.querySelector("img, script")).toBeNull();
  });

  it("tolerates a missing canvas (no interviewer attached)", () => {
    render(<CanvasPanel />);
    expect(screen.getAllByText("Not answered yet")).toHaveLength(16);
  });
});
