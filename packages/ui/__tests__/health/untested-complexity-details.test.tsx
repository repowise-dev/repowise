import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { BiomarkerDetails } from "../../src/health/biomarker-details.js";
import { biomarkerInfo, ADVISORY_HOME_BIOMARKERS } from "../../src/health/biomarker-glossary.js";

describe("untested_complexity details", () => {
  it("shows a measured percentage", () => {
    render(
      <BiomarkerDetails
        biomarkerType="untested_complexity"
        details={{ crap: 56, ccn: 7, coverage_basis: "measured", coverage_pct: 0 }}
      />,
    );
    expect(screen.getByText("CRAP 56 · complexity 7 · 0% covered")).toBeInTheDocument();
  });

  it("shows an estimate as its tier, never a percentage", () => {
    render(
      <BiomarkerDetails
        biomarkerType="untested_complexity"
        details={{ crap: 43, ccn: 12, coverage_basis: "estimated", coverage_tier: "reached", action: "add_tests" }}
      />,
    );
    const line = screen.getByText(/CRAP 43/);
    expect(line.textContent).toBe("CRAP 43 · complexity 12 · estimated: tests reach it");
    expect(line.textContent).not.toContain("%");
  });

  it("has a glossary entry and an advisory home", () => {
    expect(biomarkerInfo("untested_complexity").category).toBe("test_coverage");
    expect(ADVISORY_HOME_BIOMARKERS.has("untested_complexity")).toBe(true);
  });
});
