import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatCard } from "../../src/shared/stat-card.js";

describe("StatCard", () => {
  it("renders the label and value", () => {
    render(<StatCard label="Hotspots" value="42" />);
    expect(screen.getByText("Hotspots")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("renders an optional description when provided", () => {
    render(<StatCard label="Coverage" value="87%" description="last 30 days" />);
    expect(screen.getByText("last 30 days")).toBeInTheDocument();
  });

  it("renders a positive trend with an up arrow", () => {
    render(
      <StatCard
        label="Hotspots"
        value={42}
        trend={{ value: "+5", positive: true }}
      />,
    );
    expect(screen.getByText(/↑ \+5/)).toBeInTheDocument();
  });
});
