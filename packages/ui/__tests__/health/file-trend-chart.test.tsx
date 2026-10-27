import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { FileHealthTrend } from "@repowise-dev/types/health";
import { FileTrendChart } from "../../src/health/file-trend-chart.js";

function trend(partial: Partial<FileHealthTrend>): FileHealthTrend {
  return {
    file_path: "a.py",
    points: [],
    current: null,
    previous: null,
    delta: null,
    declining: false,
    snapshot_count: 0,
    ...partial,
  };
}

describe("FileTrendChart", () => {
  it("renders the chart and delta when history is present", () => {
    render(
      <FileTrendChart
        trend={trend({
          points: [
            { taken_at: "2026-01-01T00:00:00Z", score: 8 },
            { taken_at: "2026-01-02T00:00:00Z", score: 6.5 },
          ],
          current: 6.5,
          previous: 8,
          delta: -1.5,
          snapshot_count: 2,
        })}
      />,
    );
    expect(screen.getByRole("img", { name: /score over time/i })).toBeInTheDocument();
    expect(screen.getByText(/-1\.50 vs\. previous/)).toBeInTheDocument();
  });

  it("flags a declining trajectory", () => {
    render(
      <FileTrendChart
        trend={trend({
          points: [
            { taken_at: null, score: 9 },
            { taken_at: null, score: 8 },
            { taken_at: null, score: 7 },
          ],
          current: 7,
          previous: 8,
          delta: -1,
          declining: true,
          snapshot_count: 3,
        })}
      />,
    );
    expect(screen.getByText("Declining")).toBeInTheDocument();
  });

  it("shows a 'no history yet' state below two points", () => {
    render(
      <FileTrendChart trend={trend({ points: [{ taken_at: null, score: 8 }], snapshot_count: 1 })} />,
    );
    expect(screen.getByText(/No score history yet/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("renders the empty state for a null trend", () => {
    render(<FileTrendChart trend={null} />);
    expect(screen.getByText(/No score history yet/)).toBeInTheDocument();
  });

  describe("below the score floor", () => {
    // The score clamps at 1.0, so the worst files in a repo all print 1.0 and
    // their line is flat however much of the work gets done. The server sends
    // the pre-clamp value for exactly those files.
    const floored = trend({
      points: [
        { taken_at: null, score: 1, unclamped_score: -2.9 },
        { taken_at: null, score: 1, unclamped_score: -0.4 },
      ],
      current: 1,
      previous: 1,
      delta: 0,
      unclamped_delta: 2.5,
      snapshot_count: 2,
    });

    it("reports the movement the floor hides instead of a flat 0", () => {
      render(<FileTrendChart trend={floored} />);
      expect(screen.getByText(/\+2\.50 vs\. previous/)).toBeInTheDocument();
      // The number the clamped delta would have produced must not appear —
      // "0.00 vs. previous" beside a real improvement is the original bug.
      expect(screen.queryByText(/0\.00 vs\. previous/)).not.toBeInTheDocument();
    });

    it("explains why the displayed score has not moved", () => {
      render(<FileTrendChart trend={floored} />);
      expect(screen.getByText(/scores below the 1\.0 floor/i)).toBeInTheDocument();
    });

    it("extends the axis under the floor and marks it", () => {
      const { container } = render(<FileTrendChart trend={floored} />);
      // The domain has to reach the deepest point, else the line is drawn
      // outside the plot area and reads as flat along the bottom edge.
      const ticks = Array.from(container.querySelectorAll("text")).map((t) => t.textContent);
      expect(ticks).toContain("-3");
      expect(ticks).toContain("score floor");
    });

    it("marks the floor for a file that is only just below it", () => {
      // 9 to 10 points of deduction puts the unclamped score inside [0, 1):
      // below the floor, but not below zero. Gating the marker on a negative
      // axis minimum captioned this file as floored while drawing no floor.
      const { container } = render(
        <FileTrendChart
          trend={trend({
            points: [
              { taken_at: null, score: 1, unclamped_score: 0.9 },
              { taken_at: null, score: 1, unclamped_score: 0.4 },
            ],
            delta: 0,
            unclamped_delta: -0.5,
            snapshot_count: 2,
          })}
        />,
      );
      expect(screen.getByText(/scores below the 1\.0 floor/i)).toBeInTheDocument();
      const ticks = Array.from(container.querySelectorAll("text")).map((t) => t.textContent);
      expect(ticks).toContain("score floor");
    });

    it("leaves an ordinary chart alone", () => {
      const { container } = render(
        <FileTrendChart
          trend={trend({
            points: [
              { taken_at: null, score: 8, unclamped_score: 8 },
              { taken_at: null, score: 6.5, unclamped_score: 6.5 },
            ],
            delta: -1.5,
            unclamped_delta: -1.5,
            snapshot_count: 2,
          })}
        />,
      );
      const ticks = Array.from(container.querySelectorAll("text")).map((t) => t.textContent);
      expect(ticks).not.toContain("score floor");
      expect(screen.queryByText(/scores below the 1\.0 floor/i)).not.toBeInTheDocument();
    });

    it("falls back to the plain score when the server sends no depth", () => {
      // Hosted, and every row written before deductions were captured. A
      // floored file stays flat rather than acquiring an invented depth.
      const { container } = render(
        <FileTrendChart
          trend={trend({
            points: [
              { taken_at: null, score: 1 },
              { taken_at: null, score: 1 },
            ],
            delta: 0,
            snapshot_count: 2,
          })}
        />,
      );
      const ticks = Array.from(container.querySelectorAll("text")).map((t) => t.textContent);
      expect(ticks).not.toContain("score floor");
      expect(screen.queryByText(/vs\. previous/)).not.toBeInTheDocument();
    });
  });
});
