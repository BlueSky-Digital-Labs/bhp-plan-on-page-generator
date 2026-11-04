import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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

describe("CanvasPanel", () => {
  it("lists every canvas field in template order, unanswered by default", () => {
    render(<CanvasPanel canvas={view({ missing_required: Array(14).fill("x") })} />);
    const labels = screen.getAllByRole("term").map((t) => t.textContent);
    expect(labels).toEqual([
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
    expect(screen.getByTestId("canvas-progress")).toHaveTextContent("14 required answers still to go.");
  });

  it("covers exactly the fields the shared canvas definition requires", () => {
    // 14 required answers on the server must equal what the form's own validator counts.
    expect(missingRequired(emptyCanvas())).toHaveLength(14);
  });

  it("shows the saved values, including more than one opportunity type", () => {
    render(
      <CanvasPanel
        canvas={view({
          fields: { solutionName: "Shift Notes Assistant", opportunityTypes: ["Find Information", "Generate Reports"], priority: "High" },
          missing_required: Array(11).fill("x"),
        })}
      />,
    );
    expect(row("solutionName").getByText("Shift Notes Assistant")).toBeInTheDocument();
    expect(row("opportunityTypes").getByText("Find Information, Generate Reports")).toBeInTheDocument();
    expect(row("priority").getByText("High")).toBeInTheDocument();
    expect(screen.getByTestId("canvas-progress")).toHaveTextContent("11 required answers still to go.");
  });

  it("says when the canvas is ready for review, and uses the singular for one remaining answer", () => {
    const { rerender } = render(<CanvasPanel canvas={view({ missing_required: ["team"] })} />);
    expect(screen.getByTestId("canvas-progress")).toHaveTextContent("1 required answer still to go.");
    rerender(<CanvasPanel canvas={view({ ready_for_review: true })} />);
    expect(screen.getByTestId("canvas-progress")).toHaveTextContent("Ready for review");
  });

  it("renders saved values as inert text, never as markup", () => {
    const hostile = '<img src=x onerror="alert(1)"> <script>alert(2)</script>';
    const { container } = render(<CanvasPanel canvas={view({ fields: { painPoints: hostile } })} />);
    expect(row("painPoints").getByText(hostile)).toBeInTheDocument();
    expect(container.querySelector("img, script")).toBeNull();
  });

  it("tolerates a missing canvas (no interviewer attached)", () => {
    render(<CanvasPanel />);
    expect(screen.getAllByText("Not answered yet")).toHaveLength(16);
  });

  it("offers 'Generate canvas' only when the server says the canvas is ready", async () => {
    const onGenerate = vi.fn();
    const { rerender } = render(<CanvasPanel canvas={view({ missing_required: ["team"] })} onGenerate={onGenerate} />);
    expect(screen.queryByRole("button", { name: "Generate canvas" })).not.toBeInTheDocument();

    rerender(<CanvasPanel canvas={view({ ready_for_review: true })} onGenerate={onGenerate} />);
    await userEvent.click(screen.getByRole("button", { name: "Generate canvas" }));
    expect(onGenerate).toHaveBeenCalledTimes(1);
  });

  it("never shows the button without a handler, however the data looks", () => {
    render(<CanvasPanel canvas={view({ ready_for_review: true })} />);
    expect(screen.queryByRole("button", { name: "Generate canvas" })).not.toBeInTheDocument();
  });
});
