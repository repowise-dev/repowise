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
});
