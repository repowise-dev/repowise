import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { HealthDistribution } from "@repowise-dev/types/health";
import { HealthDistributionBar } from "../../src/health/health-distribution-bar.js";
import { HealthBadge } from "../../src/health/health-badge.js";

const DIST: HealthDistribution = {
  total_files: 10,
  total_nloc: 1000,
  bands: {
    excellent: { files: 4, nloc: 500, pct: 50 },
    good: { files: 2, nloc: 200, pct: 20 },
    fair: { files: 2, nloc: 150, pct: 15 },
    needs_work: { files: 1, nloc: 50, pct: 5 },
    at_risk: { files: 1, nloc: 100, pct: 10 },
  },
};

describe("HealthDistributionBar", () => {
  it("renders the NLOC-weighted per-band shares", () => {
    render(<HealthDistributionBar distribution={DIST} />);
    expect(screen.getByText(/50% excellent/)).toBeInTheDocument();
    expect(screen.getByText(/20% good/)).toBeInTheDocument();
    expect(screen.getByText(/15% fair/)).toBeInTheDocument();
    expect(screen.getByText(/5% needs work/)).toBeInTheDocument();
    expect(screen.getByText(/10% at risk/)).toBeInTheDocument();
  });

  it("shows an empty state when no files are analyzed", () => {
    const empty: HealthDistribution = {
      total_files: 0,
      total_nloc: 0,
      bands: {
        excellent: { files: 0, nloc: 0, pct: 0 },
        good: { files: 0, nloc: 0, pct: 0 },
        fair: { files: 0, nloc: 0, pct: 0 },
        needs_work: { files: 0, nloc: 0, pct: 0 },
        at_risk: { files: 0, nloc: 0, pct: 0 },
      },
    };
    render(<HealthDistributionBar distribution={empty} />);
    expect(screen.getByText("No files analyzed.")).toBeInTheDocument();
  });

  it("renders what a server predating the five bands sends, rather than throwing", () => {
    // The CLI/server and the app that reads it upgrade independently, so a
    // three-band payload is reachable. A missing band reads as absent.
    const partial = {
      total_files: 10,
      total_nloc: 1000,
      bands: { excellent: { files: 6, nloc: 700, pct: 70 } },
    } as unknown as HealthDistribution;
    render(<HealthDistributionBar distribution={partial} />);
    expect(screen.getByText(/70% excellent/)).toBeInTheDocument();
    expect(screen.getByText(/0% at risk/)).toBeInTheDocument();
  });

  it("does not report a repo with a three-band payload as empty in every band", () => {
    // A server predating the five bands sends correct totals under band names
    // this build does not know. Rendering that as five zeroes is a confident
    // false statement — "we analysed 128 files and none is in any band".
    const legacy = {
      total_files: 128,
      total_nloc: 9000,
      bands: {
        healthy: { files: 100, nloc: 7000, pct: 78 },
        warning: { files: 20, nloc: 1500, pct: 17 },
        alert: { files: 8, nloc: 500, pct: 5 },
      },
    } as unknown as HealthDistribution;
    render(<HealthDistributionBar distribution={legacy} />);
    expect(screen.getByText("No files analyzed.")).toBeInTheDocument();
    expect(screen.queryByText(/0% at risk/)).not.toBeInTheDocument();
  });
});

describe("HealthBadge", () => {
  it("renders the score and derives the band when none is passed", () => {
    render(<HealthBadge score={2.5} />);
    const el = screen.getByText("2.5");
    expect(el).toBeInTheDocument();
    // At risk band -> error color class.
    expect(el.className).toContain("color-error");
  });

  it("renders nothing for a missing score", () => {
    const { container } = render(<HealthBadge score={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("falls back to the score when the API sends a band this build does not know", () => {
    // A server predating the five bands sends "healthy". Indexing the colour
    // table with it yields undefined, and the pill renders unstyled.
    render(<HealthBadge score={2.5} band={"healthy" as never} />);
    const el = screen.getByText("2.5");
    expect(el.className).toContain("color-error");
    expect(el.className).not.toContain("undefined");
  });
});
