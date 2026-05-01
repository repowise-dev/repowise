import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DecisionHealthWidget } from "../../src/decisions/decision-health-widget.js";
import type { DecisionHealth } from "@repowise/types/decisions";

const HEALTH: DecisionHealth = {
  summary: { active: 12, proposed: 3, deprecated: 1, superseded: 2, stale: 4 },
  stale_decisions: [],
  proposed_awaiting_review: [],
  ungoverned_hotspots: [],
};

describe("DecisionHealthWidget", () => {
  it("renders nothing while health is undefined", () => {
    const { container } = render(<DecisionHealthWidget health={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders three stat cards with the summary counts", () => {
    render(<DecisionHealthWidget health={HEALTH} />);
    expect(screen.getByText("Active Decisions")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("Proposed")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });
});
