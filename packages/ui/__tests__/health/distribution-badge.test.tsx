import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { HealthDistribution } from "@repowise-dev/types/health";
import { HealthDistributionBar } from "../../src/health/health-distribution-bar.js";
import { HealthBadge } from "../../src/health/health-badge.js";

const DIST: HealthDistribution = {
  total_files: 10,
  total_nloc: 1000,
  bands: {
    healthy: { files: 6, nloc: 700, pct: 70 },
    warning: { files: 3, nloc: 200, pct: 20 },
    alert: { files: 1, nloc: 100, pct: 10 },
  },
};

describe("HealthDistributionBar", () => {
  it("renders the NLOC-weighted per-band shares", () => {
    render(<HealthDistributionBar distribution={DIST} />);
    expect(screen.getByText(/70% healthy/)).toBeInTheDocument();
    expect(screen.getByText(/20% warning/)).toBeInTheDocument();
    expect(screen.getByText(/10% alert/)).toBeInTheDocument();
  });

  it("shows an empty state when no files are analyzed", () => {
    const empty: HealthDistribution = {
      total_files: 0,
      total_nloc: 0,
      bands: {
        healthy: { files: 0, nloc: 0, pct: 0 },
        warning: { files: 0, nloc: 0, pct: 0 },
        alert: { files: 0, nloc: 0, pct: 0 },
      },
    };
    render(<HealthDistributionBar distribution={empty} />);
    expect(screen.getByText("No files analyzed.")).toBeInTheDocument();
  });
});

describe("HealthBadge", () => {
  it("renders the score and derives the band when none is passed", () => {
    render(<HealthBadge score={2.5} />);
    const el = screen.getByText("2.5");
    expect(el).toBeInTheDocument();
    // Alert band → error color class.
    expect(el.className).toContain("color-error");
  });

  it("renders nothing for a missing score", () => {
    const { container } = render(<HealthBadge score={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
