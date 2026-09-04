import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { PerformanceView } from "../../src/health/performance-view";
import { adapter, legacyPage, opportunity, page, resolvedDetail } from "./fixtures/performance";

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

const rows = () => screen.findAllByRole("listitem");
const openFirstRow = async () => {
  const [first] = await rows();
  fireEvent.click(first!);
  return first!;
};

describe("PerformanceView queue", () => {
  it("leads with the canonical rollup rather than flat context counters", async () => {
    render(<PerformanceView adapter={adapter()} />);
    expect(await screen.findByText("Causal opportunities")).toBeTruthy();
    expect(screen.getByText("a named safe intervention")).toBeTruthy();
    expect(screen.getByText("read the evidence first")).toBeTruthy();
    expect(screen.getByText("of 7 causes")).toBeTruthy();
  });

  it("keeps the four canonical contexts separate and counts them from the facets", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await rows();
    expect(screen.getByRole("tab", { name: /Production 4/ })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Tooling 1/ })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Test suite 2/ })).toBeTruthy();
    // Absent from the facets, so it counts zero and still gets its own tab.
    expect(screen.getByRole("tab", { name: /Unclassified 0/ })).toBeTruthy();
  });

  it("asks the server for a canonical context and never the retired pairing", async () => {
    const load = vi.fn(async () => page());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    fireEvent.click(await screen.findByRole("tab", { name: /Tooling 1/ }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith({
        context: "tooling",
        view: "detail",
        sort: "rank",
        limit: 20,
        offset: 0,
      }),
    );
  });

  it("builds the narrowing filters from the server facets and refetches on change", async () => {
    const load = vi.fn(async () => page());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    const boundary = screen.getByLabelText("Boundary");
    expect(within(boundary).getByRole("option", { name: "Database (4)" })).toBeTruthy();
    expect(within(boundary).getByRole("option", { name: "In-process (1)" })).toBeTruthy();

    fireEvent.change(boundary, { target: { value: "db" } });
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(expect.objectContaining({ boundary: "db" })),
    );
  });

  it("states a facet with one value instead of offering a control that cannot narrow", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await rows();
    expect(screen.queryByLabelText("Evidence confidence")).toBeNull();
    expect(screen.getByText("on 7 matching")).toBeTruthy();
  });

  it("states the rendered scope, the filtered total, and the repository total", async () => {
    render(<PerformanceView adapter={adapter()} />);
    // The per-page range belongs to the pagination control; this states what
    // the selection covers and when it was computed, and says each once.
    expect(await screen.findByText(/Showing/)).toBeTruthy();
    const scope = screen
      .getAllByRole("status")
      .map((node) => node.textContent ?? "")
      .find((text) => text.includes("opportunities"));
    expect(scope).toContain("7 production opportunities");
    expect(scope).toContain("848a8f1");
  });

  it("groups the page into actionability sections without reordering it", async () => {
    render(<PerformanceView adapter={adapter()} />);
    const items = await rows();
    expect(items).toHaveLength(3);
    expect(screen.getByText(/1 in the repository/)).toBeTruthy();
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent ?? "");
    expect(headings[0]).toContain("Plan ready");
    expect(headings[1]).toContain("Advisory");
    expect(headings[2]).toContain("Needs investigation");
  });

  it("shows the cause in words with the sink as separate monospace evidence", async () => {
    render(<PerformanceView adapter={adapter()} />);
    const [first] = await rows();
    expect(within(first!).getByText("Database call inside a loop")).toBeTruthy();
    expect(within(first!).getByText("src/db.py::fetch")).toBeTruthy();
    expect(within(first!).getByText(/2 call sites across 2 files/)).toBeTruthy();
    expect(within(first!).getByText(/High evidence confidence/)).toBeTruthy();
  });

  it("pages with the server cursor and never filters the page on the client", async () => {
    const load = vi.fn(async () => page());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 3 })),
    );
  });
});

describe("PerformanceView states", () => {
  it("says an unanalyzed index is unanalyzed, never clear", async () => {
    const empty = page({
      items: [],
      total: 0,
      has_more: false,
      next_offset: null,
      summary: {
        status: "unavailable",
        total: 0,
        with_plan_total: 0,
        detail: "This index has no performance analysis yet.",
      },
    });
    render(
      <PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => empty })} />,
    );
    expect(
      await screen.findByText("This index has not been analyzed for performance"),
    ).toBeTruthy();
  });

  it("distinguishes an analyzed empty repository from an unanalyzed one", async () => {
    // Empty in every context, not merely outside the one on screen, so the
    // answer is that nothing was found rather than that a filter hid it.
    const empty = page({
      items: [],
      total: 0,
      has_more: false,
      next_offset: null,
      summary: { ...page().summary, total: 0, repository_total: 0 },
    });
    render(
      <PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => empty })} />,
    );
    expect(await screen.findByText("No supported pattern surfaced")).toBeTruthy();
    expect(screen.getByText(/does not claim the code is fast/)).toBeTruthy();
  });

  it("offers a way back when a filter combination matches nothing", async () => {
    const load = vi.fn(async (query?: { boundary?: string }) =>
      query?.boundary ? page({ items: [], total: 0, has_more: false, next_offset: null }) : page(),
    );
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    fireEvent.change(screen.getByLabelText("Boundary"), { target: { value: "db" } });
    expect(await screen.findByText("No opportunities match these filters")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(expect.objectContaining({ context: "all" })),
    );
  });

  it("warns that a stale model can move ids", async () => {
    const stale = page({
      summary: { ...page().summary, status: "stale_model", performance_model_version: 1 },
    });
    render(
      <PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => stale })} />,
    );
    expect(await screen.findByText(/grouped by an earlier analysis model/)).toBeTruthy();
  });

  it("names a filter value the server did not recognize", async () => {
    const ignored = page({ ignored_arguments: { performance_boundary: "bogus" } });
    render(
      <PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => ignored })} />,
    );
    expect(await screen.findByText(/did not recognize/)).toBeTruthy();
    expect(screen.getByText("performance_boundary=bogus")).toBeTruthy();
  });

  it("reports a failed load without implying the repository is clean", async () => {
    const failing = vi.fn(async () => {
      throw new Error("boom");
    });
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: failing })} />);
    expect(await screen.findByText("Could not load performance opportunities")).toBeTruthy();
    expect(screen.getByText(/Nothing here says the code is fast/)).toBeTruthy();
  });
});

describe("PerformanceView drawer", () => {
  it("separates evidence confidence, actionability, and fix safety", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByText("Evidence confidence")).toBeTruthy();
    expect(within(panel).getByText("Actionability confidence")).toBeTruthy();
    expect(within(panel).getByText("Fix safety")).toBeTruthy();
    expect(within(panel).getByText("Proven")).toBeTruthy();
    expect(within(panel).getByText(/How reliably the call path resolved/)).toBeTruthy();
  });

  it("carries the exact drill-down an agent should call", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await openFirstRow();
    expect(await screen.findByText('get_health(opportunity_id="perf2_planready")')).toBeTruthy();
  });

  it("reads the detail by id when the host can, and reports the analyzed commit", async () => {
    const getDetail = vi.fn(async () => resolvedDetail());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunity: getDetail })} />);
    await openFirstRow();
    await waitFor(() =>
      expect(getDetail).toHaveBeenCalledWith("perf2_planready", { evidenceLimit: 8 }),
    );
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByText(/Analyzed at/)).toBeTruthy();
  });

  it("says a cause is no longer observed instead of showing it as open", async () => {
    const getDetail = vi.fn(async () => resolvedDetail({ lifecycle_status: "resolved" }));
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunity: getDetail })} />);
    await openFirstRow();
    expect(
      await screen.findByText(/No observation supports this cause in the current index/),
    ).toBeTruthy();
  });

  it("reports a stale id as stale rather than as no plan", async () => {
    const getDetail = vi.fn(async () =>
      resolvedDetail({
        model_state: {
          state: "stale_model",
          opportunity_id: "perf2_planready",
          requested_model_version: 1,
          performance_model_version: 2,
          refresh_required: true,
        },
      }),
    );
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunity: getDetail })} />);
    await openFirstRow();
    expect(await screen.findByText(/minted by performance model version 1/)).toBeTruthy();
  });

  it("tells an unresolvable id apart from a stale one and from an empty index", async () => {
    const getDetail = vi.fn(async () => ({
      resolved: false as const,
      opportunity_id: "perf2_planready",
      model_state: {
        state: "unrecognized" as const,
        opportunity_id: "perf2_planready",
        requested_model_version: null,
        performance_model_version: 2,
        refresh_required: false,
      },
      detail: "That is not a performance opportunity id.",
    }));
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunity: getDetail })} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByText(/does not recognize that opportunity id/)).toBeTruthy();
    expect(within(panel).getByText("That is not a performance opportunity id.")).toBeTruthy();
    expect(within(panel).queryByText(/minted by performance model version/)).toBeNull();
    expect(within(panel).queryByText(/has not been analyzed/)).toBeNull();
  });

  it("explains why there is no plan rather than leaving the section empty", async () => {
    const noPlan = page({
      items: [
        opportunity({
          opportunity_id: "perf2_noplan",
          plan_id: null,
          plan_status: "no_safe_plan",
          plan_reason: "No coherent intervention was proven.",
          fix: null,
        }),
      ],
    });
    render(
      <PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => noPlan })} />,
    );
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByText("No safe plan")).toBeTruthy();
    expect(within(panel).getByText("No coherent intervention was proven.")).toBeTruthy();
    expect(within(panel).queryByRole("link", { name: /Open the plan/ })).toBeNull();
  });

  it("distinguishes a plan that needs an index refresh from no safe plan", async () => {
    const refresh = page({
      items: [
        opportunity({
          plan_id: null,
          plan_status: "not_persisted",
          plan_reason: "A matching plan can be materialized by reindexing.",
        }),
      ],
    });
    render(
      <PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => refresh })} />,
    );
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByText("Needs an index refresh")).toBeTruthy();
    expect(within(panel).queryByText("No safe plan")).toBeNull();
  });

  it("offers the plan link only after the stored plan names this opportunity", async () => {
    const exact: RefactoringPlan = {
      id: "plan-1",
      refactoring_type: "performance_fix",
      file_path: "src/shared.py",
      target_symbol: "src/shared.py::load",
      line_start: 8,
      line_end: 12,
      plan: { opportunity_id: "perf2_planready", strategy: "batch_or_prefetch_io" },
      evidence: {},
      impact_delta: 0,
      effort_bucket: "M",
      blast_radius: {},
      confidence: "medium",
      source_biomarker: "io_in_loop",
      rank_score: 12.5,
    };
    render(<PerformanceView adapter={adapter({ getRefactoringPlan: async () => exact })} />);
    await openFirstRow();
    const link = await screen.findByRole("link", { name: "Open the plan on Refactoring" });
    expect(link.getAttribute("href")).toBe("/refactoring?plan=plan-1");
  });

  it("hands over the stored plan for a verified plan, not a re-derive instruction", async () => {
    const exact: RefactoringPlan = {
      id: "plan-1",
      refactoring_type: "performance_fix",
      file_path: "src/shared.py",
      target_symbol: "src/shared.py::load",
      line_start: 8,
      line_end: 12,
      plan: { opportunity_id: "perf2_planready", strategy: "batch_or_prefetch_io" },
      evidence: {},
      impact_delta: 0,
      effort_bucket: "M",
      blast_radius: {},
      confidence: "medium",
      source_biomarker: "io_in_loop",
      rank_score: 12.5,
    };
    render(<PerformanceView adapter={adapter({ getRefactoringPlan: async () => exact })} />);
    await openFirstRow();
    fireEvent.click(await screen.findByRole("button", { name: "Copy the plan for an agent" }));
    expect(await screen.findByText("Structured plan handoff")).toBeTruthy();
  });

  it("falls back to the evidence handoff when no plan was verified", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await openFirstRow();
    fireEvent.click(await screen.findByRole("button", { name: "Copy an agent handoff" }));
    expect(await screen.findByText("Agent handoff")).toBeTruthy();
  });

  it("rejects a stored plan whose opportunity identity does not match", async () => {
    const mismatched: RefactoringPlan = {
      id: "plan-1",
      refactoring_type: "performance_fix",
      file_path: "src/other.py",
      target_symbol: "src/other.py::work",
      line_start: null,
      line_end: null,
      plan: { opportunity_id: "a-different-opportunity" },
      evidence: {},
      impact_delta: 0,
      effort_bucket: "M",
      blast_radius: {},
      confidence: "medium",
      source_biomarker: "io_in_loop",
      rank_score: 1,
    };
    render(<PerformanceView adapter={adapter({ getRefactoringPlan: async () => mismatched })} />);
    await openFirstRow();
    expect(await screen.findByText(/no longer names this opportunity/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Open the plan on Refactoring" })).toBeNull();
  });

  it("keeps raw observations behind a click and pages them from the server", async () => {
    const getFindings = vi.fn(async () => ({
      items: [
        {
          id: "finding_1111",
          dimension: "performance" as const,
          biomarker_type: "io_in_loop",
          severity: "medium" as const,
          health_impact: 0,
          file_path: "src/a.py",
          function_name: "run",
          line_start: 10,
          line_end: 10,
          reason: "Database work repeats.",
          details: {},
          status: "open",
        },
      ],
      total: 6,
      has_more: true,
      next_offset: 1,
    }));
    render(
      <PerformanceView adapter={adapter({ getPerformanceOpportunityFindings: getFindings })} />,
    );
    await openFirstRow();
    expect(getFindings).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: /Read the raw observations/ }));
    await waitFor(() =>
      expect(getFindings).toHaveBeenCalledWith("perf2_planready", { offset: 0, limit: 50 }),
    );
  });

  it("states the preview bound when the host cannot page observations", async () => {
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunityFindings: undefined })} />);
    await openFirstRow();
    fireEvent.click(await screen.findByRole("button", { name: /Read the raw observations/ }));
    expect(await screen.findByText(/does not page the remaining observations/)).toBeTruthy();
  });
});

describe("PerformanceView compatibility", () => {
  it("collapses production and tooling only when the server predates the vocabulary", async () => {
    const load = vi.fn(async () => legacyPage());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    expect(screen.getByRole("tab", { name: /Production & tooling/ })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /Unclassified/ })).toBeNull();
    expect(screen.queryByLabelText("Boundary")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /Production & tooling/ }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: "production_tooling" }),
      ),
    );
  });

  it("re-asks with the spelling an older server accepts when a link restored a context", async () => {
    const load = vi.fn(async () => legacyPage());
    render(
      <PerformanceView
        adapter={adapter({ getPerformanceOpportunities: load })}
        initialFilters="context=production"
      />,
    );
    await rows();
    // The first request cannot know the vocabulary, so the correction is a
    // second request rather than a queue left answering the wrong question.
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: "production_tooling" }),
      ),
    );
  });

  it("falls back to bounded raw findings when the endpoint is absent", async () => {
    const missing = Object.assign(new Error("Not found"), { status: 404 });
    const listFindings = vi.fn(async () => [
      {
        id: "finding-legacy",
        dimension: "performance" as const,
        biomarker_type: "io_in_loop",
        severity: "medium" as const,
        health_impact: 0.4,
        file_path: "src/legacy.py",
        function_name: null,
        line_start: 4,
        line_end: 4,
        reason: "Repeated file read.",
        details: {},
        status: "open",
      },
    ]);
    render(
      <PerformanceView
        adapter={adapter({
          getPerformanceOpportunities: async () => {
            throw missing;
          },
          listFindings,
        })}
      />,
    );
    expect(await screen.findByText("Raw performance findings")).toBeTruthy();
    expect(listFindings).toHaveBeenCalledWith({ dimension: "performance", limit: 100 });
  });

  it("restores a shared filter state and reports every change back to the host", async () => {
    const load = vi.fn(async () => page());
    const onFiltersChange = vi.fn();
    render(
      <PerformanceView
        adapter={adapter({ getPerformanceOpportunities: load })}
        initialFilters="context=test&boundary=db"
        onFiltersChange={onFiltersChange}
      />,
    );
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: "test", boundary: "db" }),
      ),
    );
    fireEvent.click(screen.getByRole("tab", { name: /Production 4/ }));
    await waitFor(() =>
      expect(onFiltersChange).toHaveBeenLastCalledWith("context=production&boundary=db"),
    );
  });
});

describe("PerformanceView accessibility", () => {
  it("opens a row from the keyboard and names it", async () => {
    render(<PerformanceView adapter={adapter()} />);
    const [first] = await rows();
    expect(first!.getAttribute("tabindex")).toBe("0");
    expect(first!.getAttribute("aria-label")).toBe("Inspect Database call inside a loop");
    fireEvent.keyDown(first!, { key: "Enter" });
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("names every filter control and the row overflow", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await rows();
    expect(screen.getByLabelText("Boundary")).toBeTruthy();
    expect(screen.getByLabelText("Actionability")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /More actions for/ }).length).toBeGreaterThan(0);
  });

  it("announces the rendered scope through a status region", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await rows();
    const statuses = screen.getAllByRole("status").map((node) => node.textContent ?? "");
    expect(statuses.some((text) => text.includes("opportunities"))).toBe(true);
  });
});

describe("PerformanceView opened by a link", () => {
  it("opens the cause the link names, even when the queue page does not hold it", async () => {
    // The map and the file drawer mint links naming a cause by id. This
    // repository has hundreds of them, so the named one is usually not on the
    // page the filters would have loaded, and searching the page for it would
    // find nothing.
    const detail = resolvedDetail({ opportunity_id: "perf2_linked" });
    const getPerformanceOpportunity = vi.fn(async () => detail);
    render(
      <PerformanceView
        adapter={adapter({ getPerformanceOpportunity })}
        openOpportunityId="perf2_linked"
      />,
    );
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(getPerformanceOpportunity).toHaveBeenCalledWith("perf2_linked", {
      evidenceLimit: 8,
    });
  });

  it("reports the id back so an inspected cause is itself a link", async () => {
    const onOpenOpportunityChange = vi.fn();
    render(
      <PerformanceView adapter={adapter()} onOpenOpportunityChange={onOpenOpportunityChange} />,
    );
    await openFirstRow();
    // The first row of the fixture page, by its stable id.
    expect(onOpenOpportunityChange).toHaveBeenCalledWith(page().items[0]!.opportunity_id);
  });

  it("says so when the link names a cause this index cannot resolve", async () => {
    const unresolved = {
      resolved: false as const,
      opportunity_id: "perf2_retired",
      model_state: {
        state: "stale_model" as const,
        opportunity_id: "perf2_retired",
        performance_model_version: 2,
        requested_model_version: 1,
        refresh_required: true,
      },
      detail: "That cause was written by an older model.",
    };
    render(
      <PerformanceView
        adapter={adapter({ getPerformanceOpportunity: vi.fn(async () => unresolved) })}
        openOpportunityId="perf2_retired"
      />,
    );
    // Named, not silently ignored: the link is shareable and the reader may be
    // holding it somewhere else.
    expect(await screen.findByText(/perf2_retired/)).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
