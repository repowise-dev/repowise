import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { PerformanceView } from "../../src/health/performance-view";
import { buildMeasuredFunctionsPrompt } from "../../src/health/ai-prompts/measured-functions-prompt";
import { adapter, opportunity, page, resolvedDetail } from "./fixtures/performance";
import {
  evidenceAdapter,
  measured,
  measuredRow,
  measuredRun,
  NOTHING_MEASURED,
  PROD_CLAIM,
  RUNS,
  runSummary,
  routesOnly,
  statementsOnly,
  withBenchmarks,
} from "./fixtures/runtime-evidence";

configure({ asyncUtilTimeout: 5000 });
beforeAll(async () => {
  await import("../../src/health/performance/drawer");
  await import("../../src/health/ai-prompt-modal");
});

const table = () => screen.findByRole("list", { name: "Measured in cli build" });

describe("PerformanceView runtime evidence", () => {
  it("shows the run's strip, its split in the lede and its ranked functions", async () => {
    render(<PerformanceView adapter={adapter(evidenceAdapter())} />);
    const rows = within(await table()).getAllByRole("listitem");
    expect(rows).toHaveLength(5);
    expect(within(rows[0]!).getByText("render_block")).toBeTruthy();
    expect(within(rows[0]!).getByText("2.9%")).toBeTruthy();
    expect(within(rows[0]!).getByText("2.5 to 3.4% · 154 samples")).toBeTruthy();
    expect(within(rows[0]!).getByText("dana 62%")).toBeTruthy();
    // A null test map is unknown, never "no tests".
    expect(within(rows[1]!).getByText("tests unknown")).toBeTruthy();
    expect(within(rows[1]!).getByText("No static finding")).toBeTruthy();

    const strip = screen.getByText("Repo code explains").closest("dl")!;
    expect(within(strip).getByText("11.4%")).toBeTruthy();
    expect(within(strip).getByText("of 5,312 samples")).toBeTruthy();
    expect(within(strip).getByText("entry point · pyinstrument")).toBeTruthy();
    expect(within(strip).getByText("at 4f9c2a1, the indexed commit")).toBeTruthy();
    expect(within(strip).getByText("3 runs")).toBeTruthy();
    // The lede names how much of the run repo code can move.
    expect(screen.getByText(/a fix in repo code moves that share and the library calls/)).toBeTruthy();
    expect(screen.getByRole("list", { name: "Where the cli build run spent its time" })).toBeTruthy();
    expect(
      screen.getByText(/41 functions measured in test and tooling code are counted, not listed/),
    ).toBeTruthy();
  });

  it("reads one run's rows, and every row of it on Show all", async () => {
    const evidence = evidenceAdapter();
    render(<PerformanceView adapter={adapter(evidence)} />);
    await table();
    expect(evidence.getMeasured).toHaveBeenCalledWith({ limit: 0 });
    expect(evidence.getMeasured).toHaveBeenCalledWith({ runId: "run-cli", limit: 5 });
    expect(screen.getByText(/Showing 5 of 7 ranked functions/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show all 7" }));
    await waitFor(() => expect(within(screen.getByRole("list", { name: "Measured in cli build" })).getAllByRole("listitem")).toHaveLength(7));
    expect(evidence.getMeasured).toHaveBeenCalledWith({ runId: "run-cli", limit: 100 });
  });

  it("switches the workload from the lens", async () => {
    render(<PerformanceView adapter={adapter(evidenceAdapter())} />);
    await table();
    fireEvent.click(screen.getByRole("radio", { name: /cli serve/ }));
    const rows = within(await screen.findByRole("list", { name: "Measured in cli serve" })).getAllByRole("listitem");
    expect(within(rows[0]!).getByText("tokenize")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Measured cost in cli serve" })).toBeTruthy();
  });

  it("lists each benchmark with the function it measures and its figures per operation", async () => {
    const getMeasured = vi.fn(withBenchmarks(measured));
    render(<PerformanceView adapter={adapter({ ...evidenceAdapter(), getMeasured })} />);
    await table();
    const list = await screen.findByRole("list", { name: "Benchmarks in go test -bench" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("BenchmarkParse")).toBeTruthy();
    expect(within(rows[0]!).getByRole("link", { name: "Parse" })).toBeTruthy();
    expect(within(rows[0]!).getByText("1,234.5 ns/op · 12 allocs/op · 2,048 B/op")).toBeTruthy();
    // No function named: the benchmark and its numbers only, no claim.
    expect(within(rows[1]!).getByText("BenchmarkMisc/small")).toBeTruthy();
    expect(within(rows[1]!).queryByText(/measures/)).toBeNull();
    expect(within(rows[1]!).getByText("88 ns/op · 0 allocs/op")).toBeTruthy();
    expect(screen.getByText(/go test -bench · at 4f9c2a1/)).toBeTruthy();
    expect(getMeasured).toHaveBeenCalledWith({ runId: "run-gobench", limit: 5 });
    // A benchmark never leads: the lens offers only the ranked workloads.
    expect(screen.queryByRole("radio", { name: /go test -bench/ })).toBeNull();
  });

  it("shows the benchmarks when they are the only runtime evidence", async () => {
    const getMeasured = vi.fn(withBenchmarks(null));
    render(
      <PerformanceView
        adapter={adapter({ getMeasured, listEvidenceRuns: async () => ({ ...RUNS, runs: [] }) })}
      />,
    );
    const list = await screen.findByRole("list", { name: "Benchmarks in go test -bench" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "Benchmarks" })).toBeTruthy();
    expect(screen.queryByText("Repo code explains")).toBeNull();
    expect(screen.queryByRole("list", { name: /^Measured in/ })).toBeNull();
  });

  it("lists production statement rows when they are the only runtime evidence", async () => {
    const getMeasured = vi.fn(statementsOnly);
    const statementRun = runSummary({
      run_id: "run-pg",
      kind: "statement_stats",
      workload: "postgres:hosted",
      class: "production",
      environment: "production",
      tool: null,
      window: { start: null, end: null, base_commit: null, claim: PROD_CLAIM },
    });
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured,
          listEvidenceRuns: async () => ({ ...RUNS, runs: [statementRun] }),
        })}
      />,
    );
    const list = await screen.findByRole("list", { name: "Statements in postgres:hosted" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]!).getByText("explore_repos")).toBeTruthy();
    expect(within(rows[0]!).getByText("33.0%")).toBeTruthy();
    expect(within(rows[0]!).getByText("6 calls · mean 102 ms")).toBeTruthy();
    expect(
      within(rows[0]!).getByText("Check a fix: compare this row in the next postgres:hosted window"),
    ).toBeTruthy();
    expect(within(rows[1]!).getByText("346 calls · mean 0.9 ms")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Production statements" })).toBeTruthy();
    expect(screen.getByText("postgres:hosted · 42 statements · 1,854 ms stored")).toBeTruthy();
    // The window claim on the run line; the unattributed shares as one quiet line, on no row.
    expect(screen.getAllByText(PROD_CLAIM).length).toBeGreaterThan(0);
    expect(screen.getByText("On no single function: 23.0% ambiguous · 6.0% unjoined.")).toBeTruthy();
    expect(within(list).queryByText(/ambiguous/)).toBeNull();
    expect(getMeasured).toHaveBeenCalledWith({ runId: "run-pg", limit: 5 });
    // Statements never lead: no profile strip, no workload lens, no ranked table.
    expect(screen.queryByText("Repo code explains")).toBeNull();
    expect(screen.queryByRole("radio", { name: /postgres:hosted/ })).toBeNull();
    expect(screen.queryByRole("list", { name: /^Measured in/ })).toBeNull();
  });

  it("lists production route rows by handler when they are the only runtime evidence", async () => {
    const getMeasured = vi.fn(routesOnly);
    render(<PerformanceView adapter={adapter({ getMeasured, listEvidenceRuns: async () => ({ ...RUNS, runs: [] }) })} />);
    const list = await screen.findByRole("list", { name: "Routes in api" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("get_repo")).toBeTruthy();
    expect(within(rows[0]!).getByText("GET /repos/{repo_id}")).toBeTruthy();
    expect(within(rows[0]!).getByText("41.0%")).toBeTruthy();
    expect(within(rows[0]!).getByText("320 requests · mean 12.5 ms · p95 80 ms · 2 errors")).toBeTruthy();
    expect(within(rows[1]!).getByText("175 requests · mean 12.5 ms · p95 80 ms")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Production routes" })).toBeTruthy();
    expect(screen.getByText("api · 1,200 requests · 12 routes · 9,876 ms stored")).toBeTruthy();
    expect(screen.getByText("On no single function: 5.0% unjoined.")).toBeTruthy();
    expect(getMeasured).toHaveBeenCalledWith({ runId: "run-api", limit: 5 });
    // Routes never lead, and the statements group stays away.
    expect(screen.queryByRole("heading", { name: "Production statements" })).toBeNull();
    expect(screen.queryByRole("list", { name: /^Measured in/ })).toBeNull();
  });

  it("keeps a window whose statements joined no function, with its split and no list", async () => {
    const getMeasured = vi.fn(async (opts: { runId?: string; limit?: number } = {}) => {
      const view = await statementsOnly(opts);
      return { ...view, statements: { ...view.statements!, rows: [], total: 0 } };
    });
    render(
      <PerformanceView adapter={adapter({ getMeasured, listEvidenceRuns: async () => RUNS })} />,
    );
    expect(
      await screen.findByText("On no single function: 23.0% ambiguous · 6.0% unjoined."),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Production statements" })).toBeTruthy();
    expect(screen.getByText("postgres:hosted · 42 statements · 1,854 ms stored")).toBeTruthy();
    expect(screen.queryByRole("list", { name: /^Statements in/ })).toBeNull();
  });

  it("shows runtime evidence on an index with no performance analysis", async () => {
    const unanalyzed = page({
      items: [],
      total: 0,
      has_more: false,
      next_offset: null,
      summary: { status: "unavailable", total: 0, with_plan_total: 0 },
    });
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: vi.fn(statementsOnly),
          listEvidenceRuns: async () => RUNS,
          getPerformanceOpportunities: async () => unanalyzed,
        })}
      />,
    );
    // The measured statements show, and the notice speaks for the empty queue only.
    const list = await screen.findByRole("list", { name: "Statements in postgres:hosted" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("This index has not been analyzed for performance")).toBeTruthy();
    expect(screen.queryByRole("radiogroup", { name: "Scope" })).toBeNull();
  });

  it("names a production run's window on its run line and in the runs list", async () => {
    const window = { start: null, end: null, base_commit: null, claim: PROD_CLAIM };
    const evidence = evidenceAdapter();
    evidence.getMeasured.mockImplementation(async (opts = {}) => {
      const view = await measured(opts);
      return {
        ...view,
        measured: view.measured && {
          ...view.measured,
          runs: view.measured.runs.map((r) =>
            r.run_id === "run-cli" ? { ...r, workload_class: "production", window } : r,
          ),
        },
      };
    });
    evidence.listEvidenceRuns.mockImplementation(async () => ({
      ...RUNS,
      runs: RUNS.runs.map((r) => (r.run_id === "run-cli" ? { ...r, class: "production", window } : r)),
    }));
    render(<PerformanceView adapter={adapter(evidence)} />);
    await table();
    expect(screen.getByText(PROD_CLAIM)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Runs/ }));
    const runs = await screen.findByRole("table");
    expect(within(runs).getByText(PROD_CLAIM)).toBeTruthy();
    // Only the production run carries a claim.
    expect(within(runs).getAllByText(/in production over/)).toHaveLength(1);
  });

  it("adds nothing for runs that are not production and no statements", async () => {
    render(<PerformanceView adapter={adapter(evidenceAdapter())} />);
    await table();
    fireEvent.click(screen.getByRole("button", { name: /Runs/ }));
    await screen.findByRole("table");
    expect(screen.queryByText(/in production/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Production statements" })).toBeNull();
    expect(screen.queryByRole("list", { name: /^Statements in/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Production routes" })).toBeNull();
  });

  it("opens the static cause a measured function carries", async () => {
    const getPerformanceOpportunity = vi.fn(async () => resolvedDetail());
    render(<PerformanceView adapter={adapter({ ...evidenceAdapter(), getPerformanceOpportunity })} />);
    const rows = within(await table()).getAllByRole("listitem");
    fireEvent.click(within(rows[0]!).getByRole("button", { name: "1 finding" }));
    await waitFor(() =>
      expect(getPerformanceOpportunity).toHaveBeenCalledWith("perf3_render", expect.anything()),
    );
  });

  it("lists unit-test runs apart, closed until asked for", async () => {
    const evidence = evidenceAdapter();
    render(<PerformanceView adapter={adapter(evidence)} />);
    await table();
    expect(screen.queryByRole("list", { name: "Measured in pytest tests/" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Measured in tests/ }));
    const rows = within(await screen.findByRole("list", { name: "Measured in pytest tests/" })).getAllByRole("listitem");
    expect(within(rows[0]!).getByText("slugify")).toBeTruthy();
    expect(evidence.getMeasured).toHaveBeenCalledWith({ runId: "run-tests", limit: 5 });
  });

  it("lists every stored run, marking one from another commit", async () => {
    render(<PerformanceView adapter={adapter(evidenceAdapter())} />);
    await table();
    fireEvent.click(screen.getByRole("button", { name: /Runs/ }));
    const runs = await screen.findByRole("table");
    expect(within(runs).getAllByRole("row")).toHaveLength(4);
    // No run of its own workload is at the indexed commit to place it against.
    expect(within(runs).getByText("other commit")).toBeTruthy();
    expect(within(runs).getByText("1,240 tests, 2 failed")).toBeTruthy();
    expect(within(runs).getAllByText("96 of 100")).toHaveLength(3);
  });

  it("shows nothing measured when no run ranks anything, or the host cannot ask", async () => {
    const getMeasured = vi.fn(async () => NOTHING_MEASURED);
    const { unmount } = render(<PerformanceView adapter={adapter({ getMeasured })} />);
    await screen.findByText("changes worth making now");
    await waitFor(() => expect(getMeasured).toHaveBeenCalled());
    expect(screen.queryByRole("region", { name: "Runtime evidence" })).toBeNull();
    expect(screen.queryByText("Repo code explains")).toBeNull();
    unmount();

    render(<PerformanceView adapter={adapter()} />);
    await screen.findByText("changes worth making now");
    expect(screen.queryByRole("region", { name: "Runtime evidence" })).toBeNull();
  });

  it("says the ranked functions could not be read instead of dropping the table", async () => {
    const evidence = evidenceAdapter();
    evidence.getMeasured.mockImplementation(async (opts = {}) => {
      if (opts.runId) throw new Error("boom");
      return measured(opts);
    });
    render(<PerformanceView adapter={adapter(evidence)} />);
    expect(await screen.findByText(/Could not read the ranked functions/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Measured cost in cli build" })).toBeTruthy();
  });

  it("says Show all failed instead of keeping the preview as the whole list", async () => {
    const evidence = evidenceAdapter();
    evidence.getMeasured.mockImplementation(async (opts = {}) => {
      if (opts.limit === 100) throw new Error("boom");
      return measured(opts);
    });
    render(<PerformanceView adapter={adapter(evidence)} />);
    await table();
    fireEvent.click(screen.getByRole("button", { name: "Show all 7" }));
    expect(await screen.findByText(/Could not read the ranked functions/)).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Measured in cli build" })).toBeNull();
  });

  it("gives an ordering-only run a rank and no share", async () => {
    const evidence = evidenceAdapter();
    evidence.getMeasured.mockImplementation(async (opts = {}) => {
      const view = await measured(opts);
      const strip = <T extends { share: number | null; share_low: number | null; share_high: number | null }>(r: T) => ({
        ...r,
        share: null,
        share_low: null,
        share_high: null,
      });
      return {
        ...view,
        measured: view.measured && {
          ...view.measured,
          runs: view.measured.runs.map((r) => ({ ...r, ordering_only: true, repo_self_share: null })),
          rows: view.measured.rows.map(strip),
        },
      };
    });
    render(<PerformanceView adapter={adapter(evidence)} />);
    const rows = within(await table()).getAllByRole("listitem");
    expect(within(rows[0]!).getByText("rank 1")).toBeTruthy();
    expect(within(rows[0]!).queryByText("2.9%")).toBeNull();
    expect(screen.queryByText(/Bars are scaled/)).toBeNull();
  });

  it("puts a slim connect row above a queue that has causes and no evidence", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const { unmount } = render(
      <PerformanceView adapter={adapter()} runtimeEvidence={{ connected: false, found: [] }} />,
    );
    expect(await screen.findByText("Ranked by code shape.")).toBeTruthy();
    expect(screen.queryByText("Add runtime evidence to rank by measured time")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt for your agent" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0]![0]).toContain("repowise evidence setup --format json");
    unmount();

    render(<PerformanceView adapter={adapter()} runtimeEvidence={{ connected: true }} />);
    await screen.findByText("changes worth making now");
    expect(screen.queryByText("Ranked by code shape.")).toBeNull();
  });
});

describe("Runtime evidence marks", () => {
  it("marks a cause with what runtime evidence says, as a word, and nothing else", async () => {
    const base = opportunity();
    const items = [
      opportunity({
        opportunity_id: "perf2_unreached",
        runtime_evidence: { state: "not_exercised", workloads: ["cli build", "bench parse"] },
      }),
      opportunity({ opportunity_id: "perf2_measured", runtime_evidence: { state: "measured", workload: "cli build" } }),
      // An older server says it on the detail view's facets.
      opportunity({ opportunity_id: "perf2_facets", facets: { ...base.facets, confirmed: "query_count" } }),
      base,
    ];
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => page({ items }) })} />);
    const row = async (id: string) =>
      (await screen.findAllByRole("listitem")).find(
        (li) => li.getAttribute("data-performance-opportunity") === id,
      )!;
    expect(within(await row("perf2_unreached")).getByText("Not exercised")).toBeTruthy();
    expect(within(await row("perf2_unreached")).getByText("in cli build, bench parse")).toBeTruthy();
    expect(within(await row("perf2_measured")).getByText("Measured")).toBeTruthy();
    expect(within(await row("perf2_measured")).getByText("in cli build")).toBeTruthy();
    expect(within(await row("perf2_facets")).getByText("Confirmed")).toBeTruthy();
    expect(within(await row("perf2_facets")).getByText("by query counts that grow")).toBeTruthy();
    expect(screen.getAllByText(/^(Measured|Confirmed|Not exercised)$/)).toHaveLength(3);
  });

  it("labels evidence from a unit-test run as measured in tests", async () => {
    const items = [
      opportunity({
        opportunity_id: "perf2_in_tests",
        runtime_evidence: { state: "measured", basis: "measured_in_tests", workload: "pytest" },
      }),
      opportunity({
        opportunity_id: "perf2_confirmed_in_tests",
        runtime_evidence: { state: "confirmed", basis: "measured_in_tests" },
      }),
    ];
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => page({ items }) })} />);
    const row = async (id: string) =>
      (await screen.findAllByRole("listitem")).find(
        (li) => li.getAttribute("data-performance-opportunity") === id,
      )!;
    expect(within(await row("perf2_in_tests")).getByText("Measured in tests")).toBeTruthy();
    expect(within(await row("perf2_in_tests")).getByText("in pytest")).toBeTruthy();
    expect(within(await row("perf2_confirmed_in_tests")).getByText("Confirmed")).toBeTruthy();
    expect(within(await row("perf2_confirmed_in_tests")).getByText("by a measured test run")).toBeTruthy();
  });

  it("lists the slowest timed tests the route returns, and nothing without them", async () => {
    const getEvidenceTests = vi.fn(async () => ({
      status: "measured" as const,
      command: null,
      runs: [],
      slow_tests: [
        {
          test_id: "tests/test_site.py::test_build",
          test_file: "tests/test_site.py",
          duration_s: 4.213,
          outcome: "failed",
          first_in_class: false,
          workload: "pytest",
        },
      ],
      tests_total: 1240,
      cost: null,
    }));
    const { unmount } = render(
      <PerformanceView adapter={adapter({ ...evidenceAdapter(), getEvidenceTests })} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Test time/ }));
    expect(screen.getByText(/1,240 timed tests/)).toBeTruthy();
    expect(screen.getByText("tests/test_site.py::test_build")).toBeTruthy();
    expect(screen.getByText("4.2 s")).toBeTruthy();
    expect(screen.getByText("failed")).toBeTruthy();
    expect(getEvidenceTests).toHaveBeenCalledWith({ limit: 10 });
    unmount();

    render(<PerformanceView adapter={adapter(evidenceAdapter())} />);
    await table();
    expect(screen.queryByRole("button", { name: /Test time/ })).toBeNull();
  });
});

describe("buildMeasuredFunctionsPrompt", () => {
  it("names the run's basis and each function with its share and interval", () => {
    const prompt = buildMeasuredFunctionsPrompt({
      run: measuredRun(),
      rows: [measuredRow()],
      flavor: "generic",
    });
    expect(prompt).toContain("the entry point workload `cli build` at commit `4f9c2a1`, 5,312 samples");
    expect(prompt).toContain("Repo code itself explains 11.4% of the run");
    expect(prompt).toContain(
      "1. `render_block` in `src/render/markdown.py:88`: 2.9% of the run (2.5 to 3.4%), rank 1",
    );
    expect(prompt).toContain("Symbol id: `src/render/markdown.py::render_block`");
    expect(prompt).toContain("Static performance causes on it: `perf3_render`");
    expect(prompt).toContain("Nearest tests by call graph: `tests/test_markdown.py::test_render`");
    expect(prompt).not.toContain("Benchmark to run");
    expect(prompt).not.toMatch(/—/);
  });

  it("names the tests that ran a function and the benchmark that verifies a fix", () => {
    const prompt = buildMeasuredFunctionsPrompt({
      run: measuredRun(),
      rows: [
        measuredRow({
          tests_basis: "per_test_map",
          verify: "pytest benchmarks/bench_render.py::test_render --benchmark-only",
        }),
      ],
    });
    expect(prompt).toContain("Tests that ran it: `tests/test_markdown.py::test_render`");
    expect(prompt).toContain(
      "Benchmark to run before and after: `pytest benchmarks/bench_render.py::test_render --benchmark-only`",
    );
  });

  it("leads a row with its next step and names a workload rerun as the verify", () => {
    const prompt = buildMeasuredFunctionsPrompt({
      run: measuredRun(),
      rows: [
        measuredRow({
          next_step: {
            rule: "filesystem",
            why: "90% of it is stat calls",
            next: "walk with os.scandir",
            callee: "is_file",
            share: 0.9,
          },
          verify: "repowise perf measure --save before -- cli build before the change",
          verify_basis: "workload",
        }),
      ],
    });
    expect(prompt).toContain("Next step: walk with os.scandir");
    expect(prompt).toContain("Why: 90% of it is stat calls");
    expect(prompt).toContain(
      "How to check a fix: repowise perf measure --save before -- cli build before the change",
    );
    expect(prompt).not.toContain("Benchmark to run");
    const production = buildMeasuredFunctionsPrompt({
      run: measuredRun(),
      rows: [measuredRow({ verify: "compare this row in the next prod window", verify_basis: "workload" })],
    });
    expect(production).toContain("How to check a fix: compare this row in the next prod window");
  });

  it("names production callers, with the share each measured caller drove", () => {
    const prompt = buildMeasuredFunctionsPrompt({
      run: measuredRun(),
      rows: [
        measuredRow({
          callers: ["src/cli.py::build", "src/render/page.py::render_page"],
          caller_shares: { "src/cli.py::build": 0.82 },
        }),
      ],
    });
    expect(prompt).toContain(
      "Production callers: `src/cli.py::build` (drove 82.0% of its time), `src/render/page.py::render_page`",
    );
  });

  it("gives a rank and no share for an ordering-only run", () => {
    const prompt = buildMeasuredFunctionsPrompt({
      run: measuredRun({ ordering_only: true, repo_self_share: null }),
      rows: [measuredRow({ share: null, share_low: null, share_high: null })],
    });
    expect(prompt).toContain("`src/render/markdown.py:88`: rank 1");
    expect(prompt).toContain("no share is given");
    expect(prompt).not.toContain("Repo code itself explains");
  });
});
