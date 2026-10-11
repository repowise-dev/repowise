import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { HealthTrendResponse } from "@repowise-dev/types/health";
import { TrendView } from "../../src/health/trend-view.js";

function response(partial: Partial<HealthTrendResponse>): HealthTrendResponse {
  return {
    history: [],
    summary: {
      current_hotspot_health: 5,
      current_average_health: 7,
      previous_hotspot_health: 5,
      previous_average_health: 7,
      hotspot_delta: 0,
      average_delta: 0,
    },
    alerts: [],
    file_deltas: [],
    snapshot_count: 2,
    ...partial,
  };
}

const deltas = (n: number, offset = 0) =>
  Array.from({ length: n }, (_, i) => ({
    file_path: `f${i + offset}.py`,
    before: 8,
    after: 7,
    delta: -1,
  }));

describe("TrendView — largest score changes", () => {
  // Two caps sit between the response and the picture: the server slices its
  // list, and the chart draws only the largest few of what arrives. The block
  // is headed "Largest score changes since last index" with no total, so a
  // reader cannot tell all-of-them from a truncated tail.

  it("says how many of the total it is drawing", () => {
    render(
      <TrendView data={response({ file_deltas: deltas(50), file_deltas_total: 183 })} isLoading={false} error={null} />,
    );
    expect(screen.getByText(/Showing the 18 largest of 183 files that changed\./)).toBeInTheDocument();
  });

  it("says so plainly when nothing was dropped", () => {
    render(
      <TrendView data={response({ file_deltas: deltas(4), file_deltas_total: 4 })} isLoading={false} error={null} />,
    );
    expect(screen.getByText(/All 4 files that changed\./)).toBeInTheDocument();
  });

  it("claims no total when the server sends none", () => {
    // Hosted sends no `file_deltas_total` and caps its own list, so the list
    // length is not the total. Saying "all 3" would be a guess presented as
    // a fact; the sentence describes only what is drawn.
    render(<TrendView data={response({ file_deltas: deltas(3) })} isLoading={false} error={null} />);
    expect(screen.getByText(/Showing the 3 largest changes\./)).toBeInTheDocument();
    expect(screen.queryByText(/All 3/)).not.toBeInTheDocument();
  });

  it("does not claim completeness against a backend that caps silently", () => {
    // The concrete hosted shape: 50 rows arrive, capped server-side by a
    // backend that reports no total. "All 50 files that changed" would be
    // wrong whenever more than 50 moved.
    render(<TrendView data={response({ file_deltas: deltas(50) })} isLoading={false} error={null} />);
    expect(screen.getByText(/Showing the 18 largest changes\./)).toBeInTheDocument();
    expect(screen.queryByText(/of 50/)).not.toBeInTheDocument();
    expect(screen.queryByText(/All 50/)).not.toBeInTheDocument();
  });

  it("still counts the chart's own cap when the server sent everything", () => {
    // 25 arrive, 18 are drawn, nothing was truncated server-side. Reporting
    // "All 25" here would describe the response rather than the picture.
    render(
      <TrendView data={response({ file_deltas: deltas(25), file_deltas_total: 25 })} isLoading={false} error={null} />,
    );
    expect(screen.getByText(/Showing the 18 largest of 25 files that changed\./)).toBeInTheDocument();
  });

  it("keeps the empty state rather than reporting zero of zero", () => {
    render(
      <TrendView data={response({ file_deltas: [], file_deltas_total: 0 })} isLoading={false} error={null} />,
    );
    expect(screen.getByText(/No file changed score between the last two snapshots\./)).toBeInTheDocument();
    expect(screen.queryByText(/Showing the/)).not.toBeInTheDocument();
  });
});

describe("TrendView — how a decline is reported", () => {
  // A fall the code shape did not cause is not the reader's doing. Reporting
  // it in the same red as a regression is what told someone a week of
  // refactoring had made things worse.

  const alert = (kind: string) => ({
    kind,
    metric: "average_health",
    current: 7,
    baseline: 7.6,
    delta: -0.6,
    message: "Code health dropped 0.60 points.",
  });

  // Scoped to the lead element itself. Asserting over the whole tree would
  // pass or fail on the KPI ribbon's colours, which have nothing to do with
  // how an alert is reported.
  const leadColour = (text: string) => screen.getByText(text).getAttribute("style") ?? "";

  it("paints a history-driven fall neutrally, not in the error colour", () => {
    render(
      <TrendView data={response({ alerts: [alert("history_drag")] })} isLoading={false} error={null} />,
    );
    expect(leadColour("Change history, not code.")).toContain("var(--color-text-secondary)");
  });

  it("still paints a real regression in the error colour", () => {
    render(
      <TrendView data={response({ alerts: [alert("declining")] })} isLoading={false} error={null} />,
    );
    expect(leadColour("Declining.")).toContain("var(--color-error)");
  });

  it("does not name a figure the message contradicts", () => {
    render(
      <TrendView
        data={response({
          alerts: [{ ...alert("declining"), metric: "maintainability_average" }],
        })}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.queryByText("Declining health.")).toBeNull();
  });

  it("treats an unrecognised kind as a warning rather than dropping it", () => {
    render(
      <TrendView data={response({ alerts: [alert("something_new")] })} isLoading={false} error={null} />,
    );
    expect(screen.getByText("Predicted decline.")).toBeTruthy();
  });
});
