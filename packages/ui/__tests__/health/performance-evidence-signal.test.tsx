import {
  configure,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import type {
  CostCluster,
  EvidenceRunSummary,
  MeasuredResponse,
  SuiteCostResponse,
} from "@repowise-dev/types/runtime-evidence";

import { PerformanceView } from "../../src/health/performance-view";
import {
  buildRichProfilePrompt,
  buildSlowTestsPrompt,
} from "../../src/health/ai-prompts/runtime-evidence-prompts";
import { buildMeasuredFunctionsPrompt } from "../../src/health/ai-prompts/measured-functions-prompt";
import {
  changeText,
  commitPosition,
  lowSignalCauses,
  runHistory,
  runShares,
} from "../../src/health/performance/run-signal";
import { adapter } from "./fixtures/performance";
import {
  measured,
  measuredRow,
  measuredRun,
  runSummary,
} from "./fixtures/runtime-evidence";

configure({ asyncUtilTimeout: 5000 });
beforeAll(async () => {
  await import("../../src/health/performance/drawer");
  await import("../../src/health/ai-prompt-modal");
});

const INDEXED = "23b6fe40a1";
const AHEAD = "6898318db2";
const ago = (minutes: number) =>
  new Date(Date.now() - minutes * 60_000).toISOString();

/** Our own repository's first CI runs: the shape that read "rank no production function". */
const indexedRun = runSummary({
  run_id: "run-indexed",
  workload: "repowise init --mode fast",
  environment: "linux-ci",
  tool: "pyinstrument",
  value_kind: "wall",
  unit: "s",
  commit_sha: INDEXED,
  ingested_at: ago(60),
  total_value: 982.3,
  repo_self_share: 0.133,
  wait_share: 0.581,
  library_share: 0.0,
  units: {
    total: 61,
    matched: 36,
    skipped: 25,
    outside_repo: 16,
    truncated: 0,
  },
  skipped: {
    outside_repo: 16,
    vendored: 5,
    second_signal_mismatch: 3,
    no_span: 1,
  },
});
const aheadRun = runSummary({
  ...indexedRun,
  run_id: "run-ahead",
  commit_sha: AHEAD,
  freshness: "stale",
  ingested_at: ago(10),
  total_value: 870.4,
  units: {
    total: 60,
    matched: 35,
    skipped: 25,
    outside_repo: 16,
    truncated: 0,
  },
});
const junitRun = runSummary({
  run_id: "run-junit",
  kind: "test_timing",
  workload: "pytest tests/unit",
  class: "unit_test",
  environment: "linux-ci",
  tool: "JUnit XML",
  tool_version: null,
  unit: "tests",
  commit_sha: AHEAD,
  freshness: "stale",
  ingested_at: ago(9),
  total_value: 30000,
  units: {
    total: 49132,
    matched: 30000,
    skipped: 19132,
    outside_repo: 0,
    truncated: 19104,
  },
  skipped: { units_truncated: 19104, bad_name: 28 },
  repo_self_share: null,
  wait_share: null,
  library_share: null,
});

const unranked = (status: MeasuredResponse["status"]): MeasuredResponse => ({
  basis: "unknown",
  status,
  command: null,
  measured: null,
  measured_in_tests: null,
});

const runsOf =
  (runs: EvidenceRunSummary[], reference = INDEXED) =>
  async () => ({
    reference_commit: reference,
    runs,
  });

describe("Runtime evidence: where the run sits against the index", () => {
  it("says the newest run is ahead of the index instead of blaming sample counts", async () => {
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("not_ranked"),
          listEvidenceRuns: runsOf([junitRun, aheadRun, indexedRun]),
        })}
      />,
    );
    expect(
      await screen.findByText(
        "The newest run was measured at 6898318, ahead of the indexed 23b6fe4.",
      ),
    ).toBeTruthy();
    expect(screen.getByText(/update the index to rank them now/)).toBeTruthy();
    expect(screen.getByText(/not the same as nothing being slow/)).toBeTruthy();
    expect(
      screen.queryByText("The stored runs rank no production function."),
    ).toBeNull();
    expect(screen.queryByText(/too few samples/)).toBeNull();
  });

  it("reads the engine's ahead_of_index status as the ahead notice with its command", async () => {
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => ({ ...unranked("ahead_of_index"), command: "repowise update" }),
          listEvidenceRuns: runsOf([junitRun, aheadRun, indexedRun]),
        })}
      />,
    );
    expect(
      await screen.findByText(
        "The newest run was measured at 6898318, ahead of the indexed 23b6fe4.",
      ),
    ).toBeTruthy();
    expect(screen.getByText(/not the same as nothing being slow/)).toBeTruthy();
    expect(screen.getByText("repowise update")).toBeTruthy();
  });

  it("says when a ranked run at the indexed commit is shown in its place", async () => {
    const atIndex = measuredRun({ commit: INDEXED });
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async (opts = {}) => {
            const base = await measured(opts);
            return {
              ...base,
              measured: base.measured && { ...base.measured, runs: [atIndex] },
            };
          },
          listEvidenceRuns: runsOf([
            {
              ...aheadRun,
              run_id: "run-cli-ahead",
              workload: "cli build",
              environment: "local",
            },
            runSummary({ commit_sha: INDEXED, ingested_at: ago(60) }),
          ]),
        })}
      />,
    );
    expect(
      await screen.findByText(/Showing the run at 23b6fe4 until then\./),
    ).toBeTruthy();
  });

  it("names an older commit when the newest run predates the index", async () => {
    const older = {
      ...aheadRun,
      run_id: "run-old",
      commit_sha: "a1b2c3d4e5",
      ingested_at: ago(120),
    };
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => ({
            ...unranked("stale"),
            command: "repowise evidence setup",
          }),
          listEvidenceRuns: runsOf([
            older,
            { ...indexedRun, ingested_at: ago(5) },
          ]),
        })}
      />,
    );
    expect(
      await screen.findByText(
        "Measured at a1b2c3d, older than the indexed 23b6fe4.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        /Run the workloads again at 23b6fe4 and add the results\./,
      ),
    ).toBeTruthy();
  });

  it("names the older commit for a not-ranked status too, never the sample-count reason", async () => {
    const older = { ...aheadRun, run_id: "run-old", commit_sha: "a1b2c3d4e5", ingested_at: ago(120) };
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("not_ranked"),
          listEvidenceRuns: runsOf([older, { ...indexedRun, ingested_at: ago(5) }]),
        })}
      />,
    );
    expect(await screen.findByText("Measured at a1b2c3d, older than the indexed 23b6fe4.")).toBeTruthy();
    expect(screen.getByText(/Nothing is ranked from it at this index\./)).toBeTruthy();
    expect(screen.queryByText(/has changed since/)).toBeNull();
    expect(screen.queryByText("The stored runs rank no production function.")).toBeNull();
  });

  it("keeps the no-production-function notice for runs at the indexed commit", async () => {
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("not_ranked"),
          listEvidenceRuns: runsOf([
            {
              ...indexedRun,
              units: { ...indexedRun.units, matched: 400, total: 410 },
            },
          ]),
        })}
      />,
    );
    expect(
      await screen.findByText("The stored runs rank no production function."),
    ).toBeTruthy();
    expect(screen.queryByText(/ahead of the indexed/)).toBeNull();
    expect(screen.getByText(/They have too few samples in repo code/)).toBeTruthy();
  });
});

describe("Runtime evidence: the run's header", () => {
  it("shows the unranked run's total, its split with the unattributed rest, and why its signal is low", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("not_ranked"),
          listEvidenceRuns: runsOf([aheadRun, indexedRun]),
        })}
      />,
    );
    const block = (await screen.findByText("982 s wall")).closest(
      "[data-run-overview]",
    ) as HTMLElement;
    expect(block.getAttribute("data-run-overview")).toBe("run-indexed");
    expect(
      within(block).getByText("at 23b6fe4, the indexed commit"),
    ).toBeTruthy();
    const split = within(block).getByRole("list", {
      name: "Time split of the repowise init --mode fast run",
    });
    expect(within(split).getByText("Repo code")).toBeTruthy();
    expect(within(split).getByText("13% · 131 s")).toBeTruthy();
    expect(within(split).getByText("Waiting")).toBeTruthy();
    expect(within(split).getByText("Unattributed")).toBeTruthy();
    expect(within(split).getByText("29% · 281 s")).toBeTruthy();
    expect(within(split).queryByText("Libraries")).toBeNull();

    expect(within(block).getByText("Low signal.")).toBeTruthy();
    expect(
      within(block).getByText(/58% of its time waiting, on worker processes/),
    ).toBeTruthy();
    expect(
      within(block).getByText(/only 36 of 61 profiled functions mapped/),
    ).toBeTruthy();
    fireEvent.click(
      within(block).getByRole("button", {
        name: "Copy prompt: get a profile that ranks",
      }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0]![0]).toContain(
      "py-spy record --subprocesses",
    );
  });

  it("names an unranked production run's window on its overview line", async () => {
    const claim = "in production over 2026-10-30 to 2026-11-26, deployed ee257d145f19";
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("not_ranked"),
          listEvidenceRuns: runsOf([
            aheadRun,
            {
              ...indexedRun,
              class: "production",
              window: { start: null, end: null, base_commit: null, claim },
            },
          ]),
        })}
      />,
    );
    const block = (await screen.findByText("982 s wall")).closest("[data-run-overview]") as HTMLElement;
    expect(within(block).getByText(claim)).toBeTruthy();
  });

  it("names a pruned profile from the ranked run, and copies the host's prompt", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const lowSignalPrompt = vi.fn(
      () => "Switch the CI step to the sampled recipe.",
    );
    const pruned = measuredRun({ samples: null, commit: INDEXED });
    render(
      <PerformanceView
        lowSignalPrompt={lowSignalPrompt}
        adapter={adapter({
          getMeasured: async (opts = {}) => {
            const base = await measured(opts);
            return {
              ...base,
              measured: base.measured && { ...base.measured, runs: [pruned] },
            };
          },
          listEvidenceRuns: runsOf([
            { ...indexedRun, run_id: "run-cli", workload: "cli build" },
          ]),
        })}
      />,
    );
    expect(
      await screen.findByText(
        /frames under 1% of the run pruned \(the pyinstrument speedscope export/,
      ),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Copy prompt: get a profile that ranks",
      }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0]![0]).toBe(
      "Switch the CI step to the sampled recipe.",
    );
    expect(lowSignalPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ workload: "cli build", tool: "pyinstrument" }),
    );
  });

  it("gives each ranked function its own and total time, linked to the host's symbol page", async () => {
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: vi.fn(measured),
          listEvidenceRuns: runsOf([
            {
              ...indexedRun,
              run_id: "run-cli",
              workload: "cli build",
              total_value: 1000,
            },
          ]),
        })}
      />,
    );
    const rows = within(
      await screen.findByRole("list", { name: "Measured in cli build" }),
    ).getAllByRole("listitem");
    // 2.9% self and 6.8% total of a 1,000 s run.
    expect(
      within(rows[0]!).getByText("self 29.0 s · total 68.0 s"),
    ).toBeTruthy();
    const link = within(rows[0]!).getByRole("link");
    expect(link.getAttribute("href")).toBe(
      "/symbols/src/render/markdown.py::render_block",
    );
  });

  it("falls back to the file link and the total share without a time unit or a symbol page", async () => {
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: vi.fn(measured),
          listEvidenceRuns: runsOf([runSummary()], "4f9c2a1e0b"),
          symbolHref: undefined,
        })}
      />,
    );
    const rows = within(
      await screen.findByRole("list", { name: "Measured in cli build" }),
    ).getAllByRole("listitem");
    expect(within(rows[0]!).getByText("total 6.8%")).toBeTruthy();
    expect(within(rows[0]!).getByRole("link").getAttribute("href")).toBe(
      "/files/src/render/markdown.py",
    );
  });
});

describe("Runtime evidence: runs table", () => {
  it("shows each run's change from the previous run of its workload, its trend and why the rest did not map", async () => {
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("not_ranked"),
          listEvidenceRuns: runsOf([junitRun, aheadRun, indexedRun]),
        })}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Runs/ }));
    const table = await screen.findByRole("table");
    const [, junit, ahead, indexed] = within(table).getAllByRole("row");
    expect(within(ahead!).getByText("-112 s (-11.4%)")).toBeTruthy();
    expect(within(ahead!).getByText("faster")).toBeTruthy();
    expect(within(ahead!).getByText("ahead of index")).toBeTruthy();
    expect(
      within(ahead!).getByText("2 runs, oldest 982 s, newest 870 s"),
    ).toBeTruthy();
    expect(
      within(indexed!).getByText(
        "16 outside repo, 5 vendored, 3 second signal mismatch, 1 no span",
      ),
    ).toBeTruthy();
    expect(within(indexed!).queryByText(/faster|slower/)).toBeNull();
    // A test count is not a cost, so it has no change.
    expect(
      within(junit!).getByText("28 bad name; 19,104 cut at the row cap"),
    ).toBeTruthy();
  });
});

const cluster: CostCluster = {
  files: ["packages/core/src/persistence/database.py", "packages/core/src/persistence/models.py"],
  files_total: 4,
  tests: 29,
  mean_s: 1.44,
  others_mean_s: 0.09,
  excess_s: 39.2,
  share: 0.55,
  slowest: [{ test_id: "tests/unit/evidence/test_add.py::test_a", duration_s: 3.7 }],
  fixture: {
    name: "indexed",
    file_path: "tests/unit/evidence/test_add.py",
    line: 48,
    tests: 15,
    others: 0,
  },
  within: { files: ["packages/core/src/registry/plugins.py"], files_total: 1, tests: 161 },
};

describe("Runtime evidence: test time", () => {
  const suite = (limit: number): SuiteCostResponse => ({
    status: "measured",
    command: null,
    runs: [junitRun],
    slow_tests: Array.from({ length: limit }, (_, i) => ({
      test_id: `tests/unit/test_x.py::test_${i}`,
      test_file: "tests/unit/test_x.py",
      duration_s: 12.1 - i * 0.1,
      outcome: "passed",
      first_in_class: null,
      workload: "pytest tests/unit",
    })),
    tests_total: 30000,
    cost: {
      verdict: "clusters",
      seconds: 71,
      timed: 372,
      concentration: { tests: 37, share: 0.735 },
      clusters: [cluster],
      command: null,
    },
  });

  it("ranks the groups of slow tests with their fixture, lists slow tests with their file, says what the row cap cut, and reads 50 on asking", async () => {
    const getEvidenceTests = vi.fn(
      async ({ limit = 10 }: { limit?: number } = {}) => suite(limit),
    );
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("not_ranked"),
          listEvidenceRuns: runsOf([junitRun, aheadRun, indexedRun]),
          getEvidenceTests,
        })}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Test time/ }));
    expect(
      screen.getByText("Tests take 71.0 s (372 timed tests); the slowest 37 take 74% of it."),
    ).toBeTruthy();
    const groups = screen.getByRole("list", { name: "Groups of slow tests" });
    const [row] = within(groups).getAllByRole("listitem");
    expect(within(row!).getByText("+39.2 s")).toBeTruthy();
    expect(within(row!).getByText(/29 tests at 1.4 s each/)).toBeTruthy();
    expect(within(row!).getByText("55% of the suite")).toBeTruthy();
    expect(within(row!).getByText(/and 2 more files/)).toBeTruthy();
    expect(within(row!).getByText(/Inside the 161 tests that run/)).toBeTruthy();
    expect(within(row!).getByText(/asked for by 15 of them and no other timed test/)).toBeTruthy();
    expect(within(row!).getAllByRole("link")[0]!.getAttribute("href")).toBe(
      "/files/packages/core/src/persistence/database.py",
    );

    const slow = screen.getByRole("list", { name: "Slowest tests" });
    expect(within(slow).getByText("tests/unit/test_x.py::test_0")).toBeTruthy();
    expect(within(slow).getByText("12.1 s")).toBeTruthy();

    expect(
      screen.getByText(
        /30,000 of 49,132 tests timed in pytest tests\/unit: 19,104 were cut at the stored row cap in file order, not by duration/,
      ),
    ).toBeTruthy();

    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    getEvidenceTests.mockImplementationOnce(async ({ limit = 10 } = {}) => {
      await held;
      return suite(limit);
    });
    fireEvent.click(screen.getByRole("button", { name: "Show 50" }));
    // The short lists stay while the longer read is in flight, and say so.
    expect(await screen.findByText("Reading more rows.")).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Slowest tests" })).getAllByRole("listitem")).toHaveLength(10);
    release();
    await waitFor(() =>
      expect(
        within(
          screen.getByRole("list", { name: "Slowest tests" }),
        ).getAllByRole("listitem"),
      ).toHaveLength(50),
    );
    expect(getEvidenceTests).toHaveBeenCalledWith({ limit: 50 });
    expect(screen.queryByRole("button", { name: "Show 50" })).toBeNull();
  });

  it("opens a prompt for one slow test", async () => {
    render(
      <PerformanceView
        adapter={adapter({
          getMeasured: async () => unranked("not_ranked"),
          listEvidenceRuns: runsOf([indexedRun]),
          getEvidenceTests: async () => suite(3),
        })}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Test time/ }));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Copy prompt: tests/unit/test_x.py::test_0",
      }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(
        /tests\/unit\/test_x.py::test_0, faster without losing coverage/,
      ),
    ).toBeTruthy();
  });
});

describe("Runtime evidence reads", () => {
  it("places a run against the index by ingest order", () => {
    const runs = [aheadRun, indexedRun];
    expect(commitPosition(aheadRun, runs, INDEXED)).toBe("ahead");
    expect(commitPosition(indexedRun, runs, INDEXED)).toBe("indexed");
    expect(
      commitPosition({ ...aheadRun, ingested_at: ago(200) }, runs, INDEXED),
    ).toBe("older");
    expect(commitPosition(aheadRun, [aheadRun], INDEXED)).toBe("other");
  });

  it("compares a run only with the previous run of the same workload, never a test count", () => {
    const other = {
      ...indexedRun,
      run_id: "run-other",
      workload: "bench parse",
    };
    const { changes, trends } = runHistory([
      junitRun,
      aheadRun,
      other,
      indexedRun,
    ]);
    expect(changes.get("run-ahead")!.previous.run_id).toBe("run-indexed");
    expect(changes.has("run-other")).toBe(false);
    expect(changes.has("run-junit")).toBe(false);
    expect(trends.get("run-ahead")).toEqual([982.3, 870.4]);
  });

  it("starts a new history when the profiler changes, and says no change for an equal total", () => {
    const pyspy = { ...aheadRun, tool: "py-spy" };
    expect(runHistory([pyspy, indexedRun]).changes.size).toBe(0);
    const same = runHistory([{ ...aheadRun, total_value: 982.3 }, indexedRun]).changes.get("run-ahead")!;
    expect(changeText(same, "s")).toBe("no change");
  });

  it("reads a small program whose frames nearly all mapped as enough signal", () => {
    const small = { ...indexedRun, wait_share: 0.1, units: { ...indexedRun.units, matched: 30, total: 32 } };
    expect(lowSignalCauses(small, measuredRun())).toEqual([]);
  });

  it("infers the unattributed share only when the run names every other part", () => {
    expect(runShares(indexedRun)!.unattributed).toBeCloseTo(0.286, 3);
    expect(
      runShares({ ...indexedRun, library_share: null })!.unattributed,
    ).toBeNull();
    expect(runShares({ ...indexedRun, repo_self_share: null })).toBeNull();
  });

  it("reads no low signal from a deterministic tracer or a run with enough of it", () => {
    expect(
      lowSignalCauses(
        indexedRun,
        measuredRun({ ordering_only: true, samples: null }),
      ),
    ).toEqual([]);
    expect(lowSignalCauses(runSummary(), measuredRun())).toEqual([]);
    expect(
      lowSignalCauses(indexedRun, measuredRun({ samples: null })).map(
        (c) => c.kind,
      ),
    ).toEqual(["pruned", "wait", "few_mapped"]);
  });
});

describe("Runtime evidence prompts", () => {
  it("names each low-signal cause and its fix", () => {
    const prompt = buildRichProfilePrompt({
      workload: "repowise init --mode fast",
      tool: "pyinstrument",
      causes: lowSignalCauses(indexedRun, measuredRun({ samples: null })),
    });
    expect(prompt).toContain("`repowise init --mode fast` (pyinstrument)");
    expect(prompt).toContain("Do not export with `pyinstrument -r speedscope`");
    expect(prompt).toContain("py-spy record --subprocesses");
    expect(prompt).toContain("repowise evidence status");
    expect(prompt).not.toMatch(/\u2014/);
  });

  it("names the clock a py-spy profile must be added with, once", () => {
    const withPySpy = buildRichProfilePrompt({
      workload: "w",
      tool: "pyinstrument",
      causes: lowSignalCauses(indexedRun, measuredRun({ samples: null })),
    });
    expect(withPySpy.split("--value-kind cpu").length).toBe(2);
    expect(withPySpy).toContain("`value_kind=cpu` on a CI upload");
    const pruned = buildRichProfilePrompt({
      workload: "w",
      tool: "pyinstrument",
      causes: [{ kind: "pruned", tool: "pyinstrument" }],
    });
    expect(pruned).not.toContain("--value-kind");
  });

  it("hands over slow tests, or a group of tests and the fixture they share", () => {
    const tests = buildSlowTestsPrompt({
      subject: {
        kind: "tests",
        tests: [
          {
            test_id: "tests/t.py::test_a",
            test_file: "tests/t.py",
            duration_s: 12.1,
            outcome: "failed",
            first_in_class: null,
            workload: "pytest",
          },
        ],
      },
    });
    expect(tests).toContain(
      "1. `tests/t.py::test_a` in `tests/t.py`: 12.1 s, failed (workload `pytest`)",
    );
    const group = buildSlowTestsPrompt({ subject: { kind: "cluster", cluster } });
    expect(group).toContain("## 29 tests that run the same code and take longer than the rest");
    expect(group).toContain("They take 1.4 s each against 0.09 s for the other tests: 39.2 s over that pace, 55% of the suite.");
    expect(group).toContain("`packages/core/src/persistence/models.py` and 2 more");
    expect(group).toContain("Start with fixture `indexed` (`tests/unit/evidence/test_add.py:48`): 15 of them ask for it and no other timed test does.");
    expect(group).toContain("part of the 161 tests that run `packages/core/src/registry/plugins.py`");
    expect(`${tests}${group}`).not.toMatch(/\u2014/);
  });

  it("adds a measured function's total time to its entry", () => {
    const prompt = buildMeasuredFunctionsPrompt({
      run: measuredRun(),
      rows: [measuredRow()],
    });
    expect(prompt).toContain("Total time, with what it calls: 6.8% of the run");
  });
});
