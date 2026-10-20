import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import { PerformanceView } from "../../src/health/performance-view";
import {
  adapter,
  legacyPage,
  opportunity,
  page,
  resolvedDetail,
  sibling,
} from "./fixtures/performance";

// The drawer and the prompt modal load on first open, which takes longer than
// the default second when the suite runs beside others.
configure({ asyncUtilTimeout: 5000 });
// Load the lazy chunks once up front, so no single test pays the first import.
beforeAll(async () => {
  await import("../../src/health/performance/drawer");
  await import("../../src/health/ai-prompt-modal");
});

/** The cause rows, not the chips or other list items on the page. */
const rows = async () =>
  (await screen.findAllByRole("listitem")).filter((li) =>
    li.hasAttribute("data-performance-opportunity"),
  );
const openFirstRow = async () => {
  const [first] = await rows();
  fireEvent.click(first!);
  return first!;
};

/** Open a collapsed drawer section by its toggle's name. */
function expand(panel: HTMLElement, name: string | RegExp) {
  fireEvent.click(within(panel).getByRole("button", { name }));
}

describe("PerformanceView queue", () => {
  it("leads with the scope's own count and accounts for what the scope leaves out", async () => {
    render(<PerformanceView adapter={adapter()} />);
    expect(await screen.findByText("changes worth making now")).toBeTruthy();
    expect(screen.getByText(/All in src\./)).toBeTruthy();
    // Every hidden cause is named, with a link to the scope that lists it.
    expect(screen.getByRole("button", { name: "3 more production causes are not ready to act on" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "3 are outside production code" })).toBeTruthy();
  });

  it("offers one scope control, counted by the server", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await rows();
    const scope = screen.getByRole("radiogroup", { name: "Scope" });
    expect(within(scope).getByRole("radio", { name: /Worth doing now 7/ })).toBeTruthy();
    expect(within(scope).getByRole("radio", { name: /All production 10/ })).toBeTruthy();
    expect(within(scope).getByRole("radio", { name: /Everything 13/ })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /Tooling/ })).toBeNull();
  });

  it("asks for each scope's causes, and moves to a scope from its sentence", async () => {
    const load = vi.fn(async () => page());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    expect(load).toHaveBeenLastCalledWith({
      context: "production",
      view: "summary",
      sort: "rank",
      limit: 100,
      offset: 0,
    });
    fireEvent.click(screen.getByRole("button", { name: "3 are outside production code" }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: "all", actionability: "all", proof: "all", role: "all" }),
      ),
    );
  });

  it("offers the rest of production at the end of the queue worth doing now", async () => {
    const load = vi.fn(async () => page());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    fireEvent.click(screen.getByRole("button", { name: "Show the 3 other production causes" }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: "production", actionability: "all" }),
      ),
    );
  });

  it("keeps the narrowing filters behind More filters and shows each active one as a chip", async () => {
    const load = vi.fn(async () => page());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    expect(screen.queryByLabelText("Boundary")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /More filters/ }));
    const boundary = screen.getByLabelText("Boundary");
    expect(within(boundary).getByRole("option", { name: "Database (4)" })).toBeTruthy();
    // Actionability has one control, the bar, not a second select here.
    expect(screen.queryByRole("combobox", { name: "Actionability" })).toBeNull();

    fireEvent.change(boundary, { target: { value: "db" } });
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(expect.objectContaining({ boundary: "db" })),
    );
    const chip = await screen.findByRole("button", { name: "Remove filter Boundary: Database" });
    fireEvent.click(chip);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Remove filter Boundary/ })).toBeNull(),
    );
  });

  it("states a facet with one value instead of offering a control that cannot narrow", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await rows();
    fireEvent.click(screen.getByRole("button", { name: /More filters/ }));
    expect(screen.queryByLabelText("Evidence confidence")).toBeNull();
    // Label and value only; counts belong to a select's options.
    expect(screen.getByText(/Evidence confidence:/)).toBeTruthy();
    expect(screen.queryByText(/matching/)).toBeNull();
  });

  it("draws the actionability bar from the server's counts and narrows on the server", async () => {
    const load = vi.fn(async () => page());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    const bar = screen.getByRole("radiogroup", { name: "Actionability" });
    // The facet counts, which sum to the lede, not the three loaded rows.
    expect(within(bar).getByRole("radio", { name: /All 7/ })).toBeTruthy();
    expect(within(bar).getByRole("radio", { name: /Needs investigation 4/ })).toBeTruthy();
    fireEvent.click(within(bar).getByRole("radio", { name: /Plan ready 1/ }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ actionability: "plan_ready", queue: true }),
      ),
    );
  });

  it("draws every area the server counts, with true totals and its top causes", async () => {
    const items = [
      opportunity({ opportunity_id: "a", file_path: "pkg/a/x.py", actionability_state: "advisory", rank_position: 0 }),
      opportunity({ opportunity_id: "b", file_path: "pkg/b/y.py", actionability_state: "plan_ready", rank_position: 1 }),
    ];
    const smallTop = opportunity({ opportunity_id: "c", file_path: "pkg/c/z.py", rank_position: 9 });
    const load = vi.fn(async (query?: { area?: string; limit?: number }) =>
      query?.area === "pkg/c"
        ? page({ items: [smallTop], total: 4, next_offset: null })
        : page({
            items,
            total: 10,
            next_offset: null,
            areas: {
              depth: 2,
              total: 10,
              more: 0,
              items: [
                { area: "pkg/b", total: 1, actionability: { plan_ready: 1 } },
                { area: "pkg/a", total: 5, actionability: { advisory: 5 } },
                { area: "pkg/c", total: 4, actionability: { investigate: 4 } },
              ],
            },
          }),
    );
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    const areas = screen.getAllByRole("region").map((node) => node.getAttribute("aria-label"));
    expect(areas.filter((a) => a?.startsWith("pkg/"))).toEqual([
      "pkg/b, 1 causes",
      "pkg/a, 5 causes",
      "pkg/c, 4 causes",
    ]);
    expect(screen.getByText(/Across 3 areas, led by pkg\/b\./)).toBeTruthy();
    // pkg/c has no loaded cause, so its card asks for its own top three.
    await waitFor(() =>
      expect(load).toHaveBeenCalledWith(expect.objectContaining({ area: "pkg/c", limit: 3 })),
    );
    expect(await screen.findByText(/Show all 5 in pkg\/a/)).toBeTruthy();
    // No page caveat: the counts are the scope's own.
    expect(screen.queryByText(/causes on this page/)).toBeNull();
  });

  it("opens an area on the server when a card asks to show all", async () => {
    const load = vi.fn(async () =>
      page({
        areas: {
          depth: 1,
          total: 7,
          more: 0,
          items: [
            { area: "src", total: 6, actionability: { advisory: 6 } },
            { area: "lib", total: 1, actionability: { advisory: 1 } },
          ],
        },
      }),
    );
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    fireEvent.click(screen.getByRole("button", { name: /Show all 6 in src/ }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(expect.objectContaining({ area: "src" })),
    );
    expect(await screen.findByRole("button", { name: "Remove filter Area: src" })).toBeTruthy();
  });

  it("says the accounting sentence once, never a hidden second copy", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await rows();
    const text = document.body.textContent ?? "";
    expect(text.split("are outside production code").length - 1).toBe(1);
    expect(document.querySelectorAll(".sr-only p, p.sr-only").length).toBe(0);
  });

  it("leads each row with the fix, its gain, effort and safety, and the agent prompt", async () => {
    render(<PerformanceView adapter={adapter()} />);
    const [first] = await rows();
    expect(within(first!).getByText(/Database call inside a loop in/)).toBeTruthy();
    expect(within(first!).getByText("load").tagName).toBe("CODE");
    expect(within(first!).getByText("Run the independent awaits together")).toBeTruthy();
    expect(within(first!).getByText("Proven safe")).toBeTruthy();
    expect(within(first!).getByText(/2 call sites across 2 files/)).toBeTruthy();
    expect(within(first!).getByText("Plan ready")).toBeTruthy();
    expect(
      within(first!).getByRole("button", { name: /^Copy prompt for your agent: Database call/ }),
    ).toBeTruthy();
    expect(within(first!).getByRole("link", { name: "Open plan" })).toBeTruthy();
    // The file is one verb among the row's others, behind its menu.
    expect(within(first!).queryByRole("button", { name: "Open file" })).toBeNull();
    fireEvent.click(within(first!).getByRole("button", { name: /More actions for/ }));
    expect(await screen.findByRole("button", { name: "Open the file" })).toBeTruthy();
  });

  it("keeps every prompt on the queue quiet: no filled prompt without a scope-level one", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await rows();
    const filled = screen
      .getAllByRole("button", { name: /Copy prompt/ })
      .filter((button) => button.className.split(/\s+/).includes("bg-[var(--color-model-muted)]"));
    expect(filled).toEqual([]);
    expect(screen.getByRole("button", { name: /^Copy prompt for these \d+ in / })).toBeTruthy();
  });

  it("keeps the row's tip from opening the drawer, by click or by key", async () => {
    render(<PerformanceView adapter={adapter()} />);
    const [first] = await rows();
    const tip = within(first!).getByRole("button", { name: "Why this cause sits here" });
    fireEvent.click(tip);
    fireEvent.keyDown(tip, { key: "Enter" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("offers no plan link where there is no plan, and says there is no fix yet", async () => {
    render(<PerformanceView adapter={adapter()} />);
    const items = await rows();
    const last = items[items.length - 1]!;
    expect(within(last).queryByRole("link", { name: "Open plan" })).toBeNull();
    expect(within(last).getByText(/No supported fix yet/)).toBeTruthy();
  });

  it("leaves the actionability bar out when the scope holds one state", async () => {
    const one = page({
      items: [page().items[1]!],
      total: 1,
      next_offset: null,
      facets: { ...page().facets, actionability: [{ value: "advisory", total: 1 }] },
    });
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => one })} />);
    await rows();
    expect(screen.queryByRole("radiogroup", { name: "Actionability" })).toBeNull();
  });

  it("pages with the server cursor only when the result outgrows one page", async () => {
    const load = vi.fn(async () => page({ total: 250, next_offset: 100 }));
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 100 })),
    );
  });

  it("warms a cause's detail on hover through the key the drawer reads", async () => {
    const detail = vi.fn(async () => resolvedDetail());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunity: detail })} />);
    const [first] = await rows();
    fireEvent.mouseEnter(first!);
    fireEvent.mouseEnter(first!);
    await waitFor(() => expect(detail).toHaveBeenCalledTimes(1));
    fireEvent.click(first!);
    await screen.findByRole("dialog");
    expect(detail).toHaveBeenCalledTimes(1);
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
    const empty = page({
      items: [],
      total: 0,
      has_more: false,
      next_offset: null,
      scopes: { now: 0, production: 0, all: 0 },
      summary: { ...page().summary, total: 0, repository_total: 0 },
    });
    render(
      <PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => empty })} />,
    );
    expect(
      await screen.findByText(
        "No performance changes worth making now in production code at 848a8f1",
      ),
    ).toBeTruthy();
    expect(screen.getByText(/read code shape, not latency/)).toBeTruthy();
  });

  it("names the scope of a good state and folds the left-out reasons into its sentence", async () => {
    const load = vi.fn(async () =>
      page({
        items: [],
        total: 0,
        has_more: false,
        next_offset: null,
        scopes: { now: 0, production: 5, all: 7 },
        summary: {
          ...page().summary,
          default_queue: { total: 0, excluded: { unmeasured_cost: 5, test: 2, cold_path: 0 } },
        },
      }),
    );
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    expect(await screen.findByRole("button", { name: "5 more production causes are not ready to act on" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Why left out?" }));
    expect(screen.queryByRole("button", { name: /run rarely/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "loop size unknown" }));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: "production", proof: "unproven" }),
      ),
    );
  });

  it("reads an empty scope as clean, not as a filter that failed", async () => {
    const load = vi.fn(async () =>
      page({ items: [], total: 0, has_more: false, next_offset: null, scopes: { now: 0, production: 0, all: 3 } }),
    );
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    expect(
      await screen.findByText(
        "No performance changes worth making now in production code at 848a8f1",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("No opportunities match these filters")).toBeNull();
  });

  it("offers a way back when a filter combination matches nothing", async () => {
    const load = vi.fn(async (query?: { boundary?: string }) =>
      query?.boundary ? page({ items: [], total: 0, has_more: false, next_offset: null }) : page(),
    );
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    fireEvent.click(screen.getByRole("button", { name: /More filters/ }));
    fireEvent.change(screen.getByLabelText("Boundary"), { target: { value: "db" } });
    expect(await screen.findByText("No opportunities match these filters")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await rows()).toHaveLength(3);
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
  it("numbers the queue from 1 although rank_position is stored 0-based", async () => {
    render(<PerformanceView adapter={adapter()} />);
    const first = await openFirstRow();
    expect(within(first).getByText("1")).toBeTruthy();
    const panel = await screen.findByRole("dialog");
    expand(panel, "Why it ranks here");
    expect(within(panel).getByText(/Position/).textContent).toMatch(/^Position 1 in the queue/);
  });

  it("keeps the row's queue position when the detail by id carries the repository rank", async () => {
    const getDetail = vi.fn(async () => resolvedDetail({ rank_position: 7 }));
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunity: getDetail })} />);
    await openFirstRow();
    await waitFor(() => expect(getDetail).toHaveBeenCalled());
    const panel = await screen.findByRole("dialog");
    expand(panel, "Why it ranks here");
    expect(within(panel).getByText(/Position/).textContent).toMatch(/^Position 1 in the queue/);
  });

  it("separates evidence confidence, actionability, and fix safety", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expand(panel, "What the evidence shows");
    expect(within(panel).getByText("Evidence confidence")).toBeTruthy();
    expect(within(panel).getByText("Actionability confidence")).toBeTruthy();
    expect(within(panel).getByText("Fix safety")).toBeTruthy();
    expect(within(panel).getByText("Proven")).toBeTruthy();
    expect(within(panel).getByText(/How reliably the call path resolved/)).toBeTruthy();
  });

  it("shows the loop magnitude facet and the fix's concrete api", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expand(panel, "What the evidence shows");
    expect(within(panel).getByText("Loop magnitude")).toBeTruthy();
    expect(within(panel).getByText("Grows with data")).toBeTruthy();
    expect(within(panel).getByText("self._sem")).toBeTruthy();
  });

  it("hides the loop magnitude field for a n/a magnitude", async () => {
    const getDetail = vi.fn(async () =>
      resolvedDetail({
        facets: { ...opportunity().facets, loop_magnitude: "n/a" },
      }),
    );
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunity: getDetail })} />);
    await openFirstRow();
    await waitFor(() => expect(getDetail).toHaveBeenCalled());
    const panel = await screen.findByRole("dialog");
    expand(panel, "What the evidence shows");
    expect(within(panel).queryByText("Loop magnitude")).toBeNull();
  });

  it("carries the exact drill-down an agent should call", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await openFirstRow();
    expand(await screen.findByRole("dialog"), "Hand this to an agent");
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
    expand(panel, "Hand this to an agent");
    expect(await within(panel).findByText(/Analyzed at/)).toBeTruthy();
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
      found: false as const,
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
    const panel = await screen.findByRole("dialog");
    fireEvent.click(within(panel).getByRole("button", { name: "Copy prompt for your agent" }));
    expect(await screen.findByText("Prompt for your agent")).toBeTruthy();
    expect(await screen.findByText(/chars, approx/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("Causal performance opportunity");
  });

  it("shows the plan prompt the server renders when the host serves one", async () => {
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
    const served = vi.fn(async (planId: string, flavor: string) => `served ${planId} ${flavor}`);
    render(
      <PerformanceView
        adapter={adapter({ getRefactoringPlan: async () => exact, getRefactoringPlanPrompt: served })}
      />,
    );
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    fireEvent.click(within(panel).getByRole("button", { name: "Copy prompt for your agent" }));
    expect(await screen.findByText(/served plan-1 generic/)).toBeTruthy();
    expect(served).toHaveBeenCalledWith("plan-1", "generic");
  });

  it("falls back to the evidence handoff when no plan was verified", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    fireEvent.click(within(panel).getByRole("button", { name: "Copy prompt for your agent" }));
    expect(await screen.findByText(/Causal performance opportunity/)).toBeTruthy();
  });

  it("opens the agent prompt straight from a row, without opening the drawer", async () => {
    const served = vi.fn(async (planId: string, flavor: string) => `served ${planId} ${flavor}`);
    render(<PerformanceView adapter={adapter({ getRefactoringPlanPrompt: served })} />);
    const [first] = await rows();
    fireEvent.click(within(first!).getByRole("button", { name: /^Copy prompt for your agent:/ }));
    // No plan fetch on this host, so the evidence handoff, and no drawer.
    expect(await screen.findByText(/Causal performance opportunity/)).toBeTruthy();
    expect(screen.queryByText("Show this cause on the map")).toBeNull();
    expect(served).not.toHaveBeenCalled();
  });

  it("hands a whole area to an agent as one prompt", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await rows();
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt for these 3 in src" }));
    expect(await screen.findByText(/Performance causes in `src`/)).toBeTruthy();
    expect(document.body.textContent).toContain("perf2_investigate");
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

describe("PerformanceView drawer siblings", () => {
  it("names a preferred sibling's fix as the stronger one", async () => {
    const withSiblings = page({
      items: [
        opportunity({
          opportunity_id: "perf2_planready",
          siblings: [
            sibling({
              opportunity_id: "perf2_preferred",
              relation: "preferred",
              strategy: "batch_or_prefetch_io",
            }),
            sibling({
              opportunity_id: "perf2_alt",
              relation: "alternative",
              biomarker_type: "serial_await_in_loop",
              strategy: null,
            }),
          ],
        }),
      ],
    });
    render(
      <PerformanceView
        adapter={adapter({ getPerformanceOpportunities: async () => withSiblings })}
      />,
    );
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByText(/Batch or prefetch io is the stronger fix here/)).toBeTruthy();
    expect(within(panel).getByText(/Also flagged on these lines/)).toBeTruthy();
    expect(within(panel).getByRole("button", { name: "Serial await in loop" })).toBeTruthy();
  });

  it("opens a sibling cause from the drawer, the same way a shared link would", async () => {
    const withSiblings = page({
      items: [
        opportunity({
          opportunity_id: "perf2_planready",
          siblings: [
            sibling({
              opportunity_id: "perf2_alt",
              relation: "same_site",
              biomarker_type: "serial_await_in_loop",
              strategy: null,
            }),
          ],
        }),
      ],
    });
    const getDetail = vi.fn(async (id: string) =>
      resolvedDetail({
        opportunity_id: id,
        siblings: id === "perf2_planready" ? (withSiblings.items[0]!.siblings ?? []) : [],
      }),
    );
    const onOpenOpportunityChange = vi.fn();
    render(
      <PerformanceView
        adapter={adapter({
          getPerformanceOpportunities: async () => withSiblings,
          getPerformanceOpportunity: getDetail,
        })}
        onOpenOpportunityChange={onOpenOpportunityChange}
      />,
    );
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(panel).getByRole("button", { name: "Serial await in loop" })).toBeTruthy(),
    );
    fireEvent.click(within(panel).getByRole("button", { name: "Serial await in loop" }));
    await waitFor(() =>
      expect(getDetail).toHaveBeenCalledWith("perf2_alt", { evidenceLimit: 8 }),
    );
    expect(onOpenOpportunityChange).toHaveBeenLastCalledWith("perf2_alt");
  });

  it("renders no siblings note when the opportunity carries none", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(within(panel).queryByText(/Also flagged on these lines/)).toBeNull();
    expect(within(panel).queryByText(/is the stronger fix here/)).toBeNull();
  });
});

describe("PerformanceView drawer plan steps and validation", () => {
  it("renders ordered plan steps and marks only the mechanical ones", async () => {
    const withSteps = page({
      items: [
        opportunity({
          opportunity_id: "perf2_planready",
          plan_steps: [
            {
              order: 2,
              action: "Batch the second call",
              symbol: "src/b.py::run",
              file_path: "src/b.py",
              line: 20,
              applicability: "judgment",
            },
            {
              order: 1,
              action: "Batch the first call",
              symbol: "src/a.py::run",
              file_path: "src/a.py",
              line: 10,
              applicability: "mechanical",
            },
          ],
        }),
      ],
    });
    render(
      <PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => withSteps })} />,
    );
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    const firstStepText = within(panel).getByText(/Batch the first call/).closest("li");
    const secondStepText = within(panel).getByText(/Batch the second call/).closest("li");
    expect(firstStepText?.textContent).toContain("mechanical");
    expect(secondStepText?.textContent).not.toContain("mechanical");
    const ol = firstStepText?.closest("ol");
    const liTexts = Array.from(ol?.querySelectorAll("li") ?? []).map((li) => li.textContent ?? "");
    expect(liTexts[0]).toContain("Batch the first call");
    expect(liTexts[1]).toContain("Batch the second call");
  });

  it("shows a step's own check only where the server says it differs from the plan's", async () => {
    const withSteps = page({
      items: [
        opportunity({
          opportunity_id: "perf2_planready",
          validation: {
            basis: "inferred",
            via: "call-graph",
            total: 2,
            tests: ["tests/test_a.py", "tests/test_b.py"],
            commands: ["pytest tests/test_a.py tests/test_b.py"],
          },
          plan_steps: [
            {
              order: 1,
              action: "Batch the first call",
              symbol: "src/a.py::run",
              file_path: "src/a.py",
              line: 10,
              applicability: "judgment",
              verify: {
                commands: ["pytest tests/test_a.py"],
                tests: ["tests/test_a.py"],
                coverage: "inferred",
              },
            },
            {
              order: 2,
              action: "Batch the second call",
              symbol: "src/b.py::run",
              file_path: "src/b.py",
              line: 20,
              applicability: "judgment",
            },
            {
              order: 3,
              action: "Batch the third call",
              symbol: "src/c.py::run",
              file_path: "src/c.py",
              line: 30,
              applicability: "judgment",
              verify: { commands: [], tests: [], coverage: "none" },
            },
          ],
        }),
      ],
    });
    render(
      <PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => withSteps })} />,
    );
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    const step = (text: RegExp) => within(panel).getByText(text).closest("li")!;
    const first = step(/Batch the first call/);
    expect(within(first).getByText("pytest tests/test_a.py")).toBeTruthy();
    expect(within(first).getByRole("button", { name: "Copy command" })).toBeTruthy();
    const second = step(/Batch the second call/);
    expect(within(second).queryByRole("button", { name: "Copy command" })).toBeNull();
    expect(within(second).queryByText(/No test reaches/)).toBeNull();
    const third = step(/Batch the third call/);
    expect(within(third).getByText("No test reaches this step.")).toBeTruthy();
    expect(within(third).queryByRole("button", { name: "Copy command" })).toBeNull();
  });

  it("renders the stored validation through the shared validation summary", async () => {
    const withValidation = page({
      items: [
        opportunity({
          opportunity_id: "perf2_planready",
          validation: {
            basis: "measured",
            via: "call-graph",
            total: 2,
            tests: ["tests/test_a.py"],
            commands: ["pytest tests/test_a.py"],
          },
        }),
      ],
    });
    render(
      <PerformanceView
        adapter={adapter({ getPerformanceOpportunities: async () => withValidation })}
      />,
    );
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expand(panel, /^Tests/);
    expect(within(panel).getByText(/Measured coverage/)).toBeTruthy();
    expect(within(panel).getByText(/via call graph/)).toBeTruthy();
    expect(within(panel).getByText("tests/test_a.py")).toBeTruthy();
  });

  it("reads a name-match validation honestly", async () => {
    const withValidation = page({
      items: [
        opportunity({
          opportunity_id: "perf2_planready",
          validation: {
            basis: "inferred",
            via: "name-match",
            total: 1,
            tests: ["tests/test_b.py"],
            commands: [],
          },
        }),
      ],
    });
    render(
      <PerformanceView
        adapter={adapter({ getPerformanceOpportunities: async () => withValidation })}
      />,
    );
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expand(panel, /^Tests/);
    expect(within(panel).getByText(/via name match/)).toBeTruthy();
  });

  it("says validation is unknown honestly rather than silently skipping it", async () => {
    const withValidation = page({
      items: [
        opportunity({
          opportunity_id: "perf2_planready",
          validation: { basis: "unknown", via: null, total: 0, tests: [], commands: [] },
        }),
      ],
    });
    render(
      <PerformanceView
        adapter={adapter({ getPerformanceOpportunities: async () => withValidation })}
      />,
    );
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    // No guarding test is the case a reader needs, so the section opens on it.
    const tests = within(panel).getByRole("button", { name: /^Tests/ });
    expect(tests.getAttribute("aria-expanded")).toBe("true");
    expect(tests.textContent).toContain("none guarding");
    expect(within(panel).getByText("Validation gap")).toBeTruthy();
    expect(within(panel).getByText(/Treat this as explicit validation work/)).toBeTruthy();
  });

  it("renders no validation block when the opportunity carries none", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(within(panel).queryByText(/guarding test/)).toBeNull();
    expect(within(panel).queryByRole("button", { name: /^Tests/ })).toBeNull();
    expect(within(panel).queryByText(/Validation gap/)).toBeNull();
  });
});

describe("PerformanceView sort control", () => {
  it("asks the server to sort by the chosen vocabulary", async () => {
    const load = vi.fn(async () => page());
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: load })} />);
    await rows();
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "leverage" } });
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(expect.objectContaining({ sort: "leverage" })),
    );
  });
});

describe("PerformanceView compatibility", () => {
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
        initialFilters="scope=all&context=test&boundary=db"
        onFiltersChange={onFiltersChange}
      />,
    );
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: "test", boundary: "db" }),
      ),
    );
    const scope = screen.getByRole("radiogroup", { name: "Scope" });
    fireEvent.click(within(scope).getByRole("radio", { name: /All production/ }));
    await waitFor(() =>
      expect(onFiltersChange).toHaveBeenLastCalledWith(
        "scope=production&boundary=db",
        expect.objectContaining({ scope: "production", context: null, boundary: "db" }),
      ),
    );
  });

  it("reads a link minted before scopes onto the scope that lists the same causes", async () => {
    const load = vi.fn(async () => page());
    render(
      <PerformanceView
        adapter={adapter({ getPerformanceOpportunities: load })}
        initialFilters="tab=performance&ctx=all&proof=unproven&state=advisory"
      />,
    );
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: "all", proof: "unproven", actionability: "advisory" }),
      ),
    );
    expect(await screen.findByRole("button", { name: "Remove filter Showing: Advisory" })).toBeTruthy();
  });

  it("follows a URL it did not write while it stays mounted", async () => {
    const load = vi.fn(async () => page());
    const a = adapter({ getPerformanceOpportunities: load });
    const view = (search: string) => (
      <PerformanceView adapter={a} initialFilters={search} onFiltersChange={() => undefined} />
    );
    const { rerender } = render(view("scope=all&context=test&boundary=db"));
    await rows();
    const scope = screen.getByRole("radiogroup", { name: "Scope" });
    fireEvent.click(within(scope).getByRole("radio", { name: /All production/ }));
    // The host echoes the view's own write beside its own parameters.
    rerender(view("tab=performance&scope=production&boundary=db"));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: "production", boundary: "db" }),
      ),
    );
    // A link followed (or back) replaces the state.
    rerender(view("tab=performance&scope=all&proof=unproven"));
    await waitFor(() =>
      expect(load).toHaveBeenLastCalledWith(
        expect.objectContaining({ context: "all", proof: "unproven" }),
      ),
    );
    expect(load).not.toHaveBeenLastCalledWith(expect.objectContaining({ boundary: "db" }));
  });

});

describe("PerformanceView accessibility", () => {
  it("opens a row from the keyboard and names it", async () => {
    render(<PerformanceView adapter={adapter()} />);
    const [first] = await rows();
    expect(first!.getAttribute("tabindex")).toBe("0");
    expect(first!.getAttribute("aria-label")).toBe("Inspect Database call inside a loop in load");
    fireEvent.keyDown(first!, { key: "Enter" });
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("names every filter control and the row overflow", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await rows();
    fireEvent.click(screen.getByRole("button", { name: /More filters/ }));
    expect(screen.getByLabelText("Boundary")).toBeTruthy();
    expect(screen.getByRole("radiogroup", { name: "Actionability" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /More actions for/ }).length).toBeGreaterThan(0);
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
      found: false as const,
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

describe("PerformanceView drawer, the fix itself", () => {
  it("names the unit a performance fix and states gain, effort and where to edit", async () => {
    const getDetail = vi.fn(async () =>
      resolvedDetail({
        gain_text: "one database call per loop iteration, grows with the data",
        plan_economics: { effort_bucket: "M", benefit: 1, cost: 1, risk: 1 },
        // `intervention_kind` arrives with PR 2817; read as optional until then.
        ...({ intervention_kind: "module" } as object),
        intervention_symbol: "src/jobs.py::__module__",
      }),
    );
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunity: getDetail })} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByText("Performance fix")).toBeTruthy();
    expect(
      await within(panel).findByText("one database call per loop iteration, grows with the data"),
    ).toBeTruthy();
    expect(within(panel).getByText("Effort").closest("div")!.textContent).toContain("Medium");
    expect(within(panel).getByText(/Edit top-level code,/)).toBeTruthy();
    expect(within(panel).getByText("module scope of src/jobs.py")).toBeTruthy();
    expect(within(panel).queryByText(/__module__/)).toBeNull();
  });

  it("gives every facet a glossary tooltip", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expand(panel, "What the evidence shows");
    for (const facet of ["amplification", "exposure", "leverage", "change risk", "loop magnitude"]) {
      expect(within(panel).getByRole("button", { name: `What ${facet} means` })).toBeTruthy();
    }
  });

  it("widens the excerpt up to the loop header and names the sink once", async () => {
    const lines = Array.from({ length: 30 }, (_, i) => `line ${i + 1}`);
    lines[5] = "    for row in rows:";
    const readSource = vi.fn(async () => lines.join("\n"));
    render(<PerformanceView adapter={adapter({ readSource })} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(await within(panel).findByText("for row in rows:", { exact: false })).toBeTruthy();
    expect(within(panel).getByText("line 10")).toBeTruthy();
    expect(within(panel).queryByText("line 5")).toBeNull();
    expect(within(panel).getByText("The loop starts at line 6.")).toBeTruthy();
    // The sink is named once, under the code, not again above the title.
    expect(within(panel).getAllByText("src/db.py::fetch").length).toBe(1);
  });

  it("says so when no loop header sits within reach of the call", async () => {
    const lines = Array.from({ length: 30 }, (_, i) => `line ${i + 1}`);
    const readSource = vi.fn(async () => lines.join("\n"));
    render(<PerformanceView adapter={adapter({ readSource })} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(await within(panel).findByText(/No for or while line within 15 lines/)).toBeTruthy();
  });
});

describe("performance drawer preview", () => {
  it("marks the cause when the detail response carries a preview note", async () => {
    const note = "preview, measured 40% precision on 30 judged causes, 2026-10-15";
    const getDetail = vi.fn(async () => resolvedDetail({ preview: note }));
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunity: getDetail })} />);
    await openFirstRow();
    const panel = await screen.findByRole("dialog");
    expect(await within(panel).findByRole("button", { name: `Preview: ${note}` })).toBeTruthy();
  });
});
