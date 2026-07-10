import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MetricCard } from "../../src/shared/metric-card.js";

describe("MetricCard", () => {
  it("renders the label and value", () => {
    render(<MetricCard label="Hotspots" value="42" />);
    expect(screen.getByText("Hotspots")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("renders an optional description when provided", () => {
    render(<MetricCard label="Coverage" value="87%" description="last 30 days" />);
    expect(screen.getByText("last 30 days")).toBeInTheDocument();
  });

  it("renders a positive delta with an up arrow", () => {
    render(
      <MetricCard label="Hotspots" value={42} delta={{ value: "+5", positive: true }} />,
    );
    expect(screen.getByText(/↑ \+5/)).toBeInTheDocument();
  });

  it("renders a neutral delta without an arrow or coloring", () => {
    render(
      <MetricCard
        label="Files"
        value={1200}
        delta={{ value: "+12", positive: true, neutral: true }}
      />,
    );
    expect(screen.getByText("+12")).toBeInTheDocument();
    expect(screen.queryByText(/↑/)).toBeNull();
  });
});
