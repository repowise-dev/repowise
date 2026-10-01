/**
 * The Findings queue around its rows: the history switch, Watch kept apart in
 * every grouping, the whole-set graph and its link back to the list, and bulk
 * triage.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  HealthWorkItem,
  HealthWorkQueueQuery,
  HealthWorkQueueResponse,
  ImpactEffortQuery,
  ImpactEffortResponse,
} from "@repowise-dev/types/health";

import type { CodeHealthAdapter } from "../../src/health/code-health-adapter";
import { FindingsView } from "../../src/health/findings-view";

function row(path: string, partial: Partial<HealthWorkItem> = {}): HealthWorkItem {
  return {
    file_path: path,
    score: 4,
    nloc: 120,
    primary_biomarker: "complex_method",
    primary_severity: "high",
    primary_reason: `complex in ${path}`,
    primary_function: null,
    primary_line_start: 1,
    primary_line_end: 9,
    primary_suggestion: "Reduce cyclomatic complexity.",
    primary_finding_id: `f-${path}`,
    total_impact: 1,
    finding_count: 1,
    biomarkers: ["complex_method"],
    effort_bucket: "M",
    impact_per_effort: 0.5,
    ...partial,
  };
}

function plane(paths: string[]): ImpactEffortResponse {
  return {
    points: paths.map((p, i) => ({
      file_path: p,
      effort_lines: 10 * (i + 1),
      effort_basis: "file" as const,
      recoverable_health: 1 + i,
      tier: null,
    })),
    plotted: paths.length,
    total: paths.length,
    cap: 5000,
    effort_midline_lines: 150,
    gain_midline_points: 0.5,
  };
}

function adapter(over: Partial<CodeHealthAdapter>): CodeHealthAdapter {
  return {
    cacheKey: `queue-${Math.random()}`,
    getOverview: async () => ({ files: [], biomarkers: [], summary: {} }) as never,
    listFindings: async () => [],
    updateFindingStatus: async () => undefined,
    renderFileDrawer: () => null,
    ...over,
  } as unknown as CodeHealthAdapter;
}

const page = (targets: HealthWorkItem[], extra: Partial<HealthWorkQueueResponse> = {}) => ({
  targets,
  total: targets.length,
  ...extra,
});

describe("FindingsView history signals", () => {
  it("counts the hidden history-only files and asks for them on the toggle", async () => {
    const load = vi.fn(async (_q?: HealthWorkQueueQuery) =>
      page([row("a.py")], { history_only_excluded: 3 }),
    );
    render(<FindingsView adapter={adapter({ getHealthWorkQueue: load })} />);

    expect(await screen.findByText(/3 files with only history signals hidden/)).toBeInTheDocument();
    expect(load.mock.calls.at(-1)?.[0]?.history).toBeUndefined();

    fireEvent.click(screen.getByRole("button", { name: "Show history signals" }));
    await waitFor(() => expect(load.mock.calls.at(-1)?.[0]).toMatchObject({ history: "include" }));
    expect(screen.getByRole("button", { name: "Hide history signals" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("keeps Watch rows apart when grouped by marker", async () => {
    const load = vi.fn(async () =>
      page([
        row("a.py"),
        row("w.py", { primary_biomarker: "change_entropy", biomarkers: ["change_entropy"] }),
      ]),
    );
    render(<FindingsView adapter={adapter({ getHealthWorkQueue: load })} />);
    await screen.findByText("a.py");

    fireEvent.change(screen.getByLabelText("Group"), { target: { value: "biomarker" } });

    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent ?? "");
    const watchAt = headings.findIndex((h) => h.startsWith("Watch"));
    const markerAt = headings.findIndex((h) => h.startsWith("Complex method"));
    expect(markerAt).toBeGreaterThan(-1);
    expect(watchAt).toBeGreaterThan(markerAt);
    const watchSection = screen.getAllByRole("heading", { level: 3 })[watchAt]!.closest("section")!;
    expect(within(watchSection).getByText("w.py")).toBeInTheDocument();
  });
});

describe("FindingsView graph", () => {
  it("plots the whole filtered set and asks without paging or order", async () => {
    const getImpactEffort = vi.fn(async (_q?: ImpactEffortQuery) => plane(["a.py", "b.py", "c.py"]));
    render(
      <FindingsView
        adapter={adapter({
          getHealthWorkQueue: async () => page([row("a.py")], { total: 3 }),
          getImpactEffort,
        })}
      />,
    );
    expect(await screen.findByText(/plotted of/)).toBeInTheDocument();
    const asked = getImpactEffort.mock.calls.at(-1)?.[0] ?? {};
    expect(Object.keys(asked)).not.toEqual(expect.arrayContaining(["limit"]));
    expect(asked).not.toHaveProperty("offset");
    expect(asked).not.toHaveProperty("sort");
  });

  it("highlights the clicked file's row on this page", async () => {
    render(
      <FindingsView
        adapter={adapter({
          getHealthWorkQueue: async () => page([row("a.py"), row("b.py")]),
          getImpactEffort: async () => plane(["a.py", "b.py"]),
        })}
      />,
    );
    await screen.findByText(/plotted of/);
    fireEvent.click(document.querySelector('[data-file="b.py"]')!);

    const card = document.querySelector('[data-health-work-item="b.py"]')!;
    await waitFor(() => expect(card.className).toContain("border-[var(--color-accent-primary)]"));
    expect(document.querySelector('[data-health-work-item="a.py"]')!.className).not.toContain(
      "border-[var(--color-accent-primary)]",
    );
  });

  it("fetches a clicked file that is not on this page and shows it above the list", async () => {
    const load = vi.fn(async (q?: HealthWorkQueueQuery) =>
      q?.search === "deep/c.py" ? page([row("deep/c.py")]) : page([row("a.py")], { total: 2 }),
    );
    render(
      <FindingsView
        adapter={adapter({
          getHealthWorkQueue: load,
          getImpactEffort: async () => plane(["a.py", "deep/c.py"]),
        })}
      />,
    );
    await screen.findByText(/plotted of/);
    fireEvent.click(document.querySelector('[data-file="deep/c.py"]')!);

    const pinned = await screen.findByRole("region", { name: "Selected in the graph" });
    expect(within(pinned).getByText("deep/c.py")).toBeInTheDocument();
  });

  it("draws no graph when the host cannot serve the whole set", async () => {
    render(<FindingsView adapter={adapter({ getHealthWorkQueue: async () => page([row("a.py")]) })} />);
    await screen.findByText("a.py");
    expect(screen.queryByText(/plotted of/)).not.toBeInTheDocument();
  });
});

describe("FindingsView bulk triage", () => {
  it("sets the status of each checked row's finding", async () => {
    const updateFindingStatus = vi.fn(async (_id: string, _status: string) => ({}) as never);
    render(
      <FindingsView
        adapter={adapter({
          getHealthWorkQueue: async () => page([row("a.py"), row("b.py")]),
          updateFindingStatus,
        })}
      />,
    );
    await screen.findByText("a.py");
    expect(screen.queryByRole("region", { name: "Bulk triage" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: /finding in a.py/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /finding in b.py/ }));
    const bar = screen.getByRole("region", { name: "Bulk triage" });
    fireEvent.click(within(bar).getByRole("button", { name: "Acknowledge" }));

    await waitFor(() => expect(updateFindingStatus).toHaveBeenCalledTimes(2));
    expect(updateFindingStatus).toHaveBeenCalledWith("f-a.py", "acknowledged");
    expect(updateFindingStatus).toHaveBeenCalledWith("f-b.py", "acknowledged");
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Bulk triage" })).not.toBeInTheDocument(),
    );
  });
});
