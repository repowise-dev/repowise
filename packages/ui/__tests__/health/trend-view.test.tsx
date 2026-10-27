import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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

describe("TrendView — hotspot health stat", () => {
  const noHotspots = {
    current_hotspot_health: null,
    current_average_health: 8.1,
    previous_hotspot_health: null,
    previous_average_health: 8,
    hotspot_delta: null,
    average_delta: 0.1,
  };

  it("says a repository has no hotspot files on the full scope, not that the scope hid them", () => {
    render(
      <TrendView
        data={response({ scope: "all", summary: noHotspots })}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.getByText("no hotspot files")).toBeTruthy();
    expect(screen.queryByText("not measured for this scope")).toBeNull();
  });

  it("keeps saying the figure was not measured under a narrowed scope", () => {
    render(
      <TrendView
        data={response({ scope: "production", summary: noHotspots })}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.getByText("not measured for this scope")).toBeTruthy();
    expect(screen.queryByText("no hotspot files")).toBeNull();
  });

  it("still shows the score and its delta when a hotspot figure exists", () => {
    render(
      <TrendView
        data={response({
          scope: "all",
          summary: {
            ...noHotspots,
            current_hotspot_health: 6.5,
            previous_hotspot_health: 5,
            hotspot_delta: 1.5,
          },
        })}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.getByText("6.5")).toBeTruthy();
    expect(screen.queryByText("no hotspot files")).toBeNull();
    expect(screen.queryByText("not measured for this scope")).toBeNull();
  });
});

describe("TrendView — movers", () => {
  const moved = [
    { file_path: "src/core/a.py", before: 8, after: 5, delta: -3 },
    { file_path: "src/web/b.ts", before: 4, after: 6.5, delta: 2.5 },
    { file_path: "c.py", before: 7, after: 6.5, delta: -0.5 },
  ];

  it("groups what got worse ahead of what improved, with name and folder apart", () => {
    render(
      <TrendView
        data={response({ file_deltas: moved, file_deltas_total: 3, file_deltas_worse: 2, file_deltas_improved: 1 })}
        isLoading={false}
        error={null}
      />,
    );
    const worse = screen.getByText("Got worse");
    const improved = screen.getByText("Improved");
    expect(worse.compareDocumentPosition(improved) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("a.py")).toBeInTheDocument();
    expect(screen.getByText("src/core")).toBeInTheDocument();
    expect(screen.getByText("2 got worse · 1 improved")).toBeInTheDocument();
  });

  it("puts the code-health delta on the summary line", () => {
    render(
      <TrendView
        data={response({
          file_deltas: moved,
          file_deltas_worse: 2,
          file_deltas_improved: 1,
          summary: { ...response({}).summary, average_delta: -0.3 },
        })}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.getByText("2 got worse · 1 improved · code health -0.30")).toBeInTheDocument();
  });

  it("counts only what arrived when the server sends no per-direction totals", () => {
    // Hosted sends neither count and caps its list, so the sentence names the
    // rows it counted rather than presenting them as every file that moved.
    render(<TrendView data={response({ file_deltas: moved })} isLoading={false} error={null} />);
    expect(screen.getByText("Among the 3 largest changes: 2 got worse · 1 improved")).toBeInTheDocument();
  });

  it("says how many of a group's total it lists", () => {
    render(
      <TrendView
        data={response({ file_deltas: deltas(20), file_deltas_worse: 140, file_deltas_improved: 0 })}
        isLoading={false}
        error={null}
      />,
    );
    expect(screen.getByText("8 of 140")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show 12 more" }));
    expect(screen.getByText("20 of 140")).toBeInTheDocument();
  });

  it("opens a file from its row when the host can, and is plain text when it cannot", () => {
    const onSelectFile = vi.fn();
    const { rerender } = render(
      <TrendView data={response({ file_deltas: moved })} isLoading={false} error={null} />,
    );
    expect(screen.queryByRole("button", { name: /a\.py/ })).toBeNull();
    rerender(
      <TrendView
        data={response({ file_deltas: moved })}
        isLoading={false}
        error={null}
        onSelectFile={onSelectFile}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /a\.py/ }));
    expect(onSelectFile).toHaveBeenCalledWith("src/core/a.py");
  });

  it("keeps the empty state rather than reporting zero of zero", () => {
    render(
      <TrendView data={response({ file_deltas: [], file_deltas_total: 0 })} isLoading={false} error={null} />,
    );
    expect(screen.getByText(/No file changed score between the last two snapshots\./)).toBeInTheDocument();
    expect(screen.queryByText("Got worse")).not.toBeInTheDocument();
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
