import { describe, it, expect } from "vitest";
import { notMeasured, riskLede, testsFigure, testsPlan } from "../../src/blast-radius/change-review-model.js";
import { makeReview } from "./review-fixture.js";


describe("riskLede", () => {
  it("leads with the rank and names what raised it", () => {
    const lede = riskLede(makeReview().risk);
    expect(lede.priority).toBe("high");
    expect(lede.sentence).toBe("Riskier than 82% of this repo's recent commits, by size and spread.");
    // Only drivers that raised the score, strongest first.
    expect(lede.drivers).toBe("Mostly 400 lines added and 6 files touched.");
    expect(lede.detail).toMatch(/^Change risk 6\.3 of 10 on a fixed scale/);
  });

  it("falls back to the score when there is no baseline to rank against", () => {
    const lede = riskLede(makeReview({ risk: { risk_percentile: null, review_priority: null, fallback_band: "high", drivers: [] } }).risk);
    expect(lede.priority).toBeNull();
    expect(lede.sentence).toBe("Change risk 6.3 of 10, by size and spread.");
    expect(lede.drivers).toBeNull();
  });
});

describe("testsPlan", () => {
  it("lists a selection with its command", () => {
    expect(testsPlan(makeReview())).toEqual({
      kind: "list",
      tests: ["tests/test_a.py", "tests/test_b.py"],
      total: 2,
      commands: ["pytest tests/test_a.py tests/test_b.py"],
      measured: false,
      apart: 0,
      summary: "2 tests reach the change.",
    });
  });

  it("counts the tests kept apart, and never says none need to run while they do", () => {
    const review = makeReview({
      tests: { tests_to_run: [], total: 0, broadcast_total: 2, import_closure_total: 1, summary: "3 more tests must still run." },
    });
    const plan = testsPlan(review);
    expect(plan).toMatchObject({ kind: "list", tests: [], total: 3, apart: 3 });
    expect(testsFigure(review, 0)).toEqual({ figure: "3", runAll: false, total: 3 });
  });

  it("says run everything, with the reasons, when the selection could not narrow it", () => {
    const plan = testsPlan(makeReview({ tests: { status: "run_all", run_all: true, reasons: ["conftest.py changed"], tests_to_run: [] } }));
    expect(plan).toEqual({ kind: "run_all", reasons: ["conftest.py changed"], startWith: [] });
  });

  it("treats a selection that never ran as run everything, never as no tests", () => {
    const plan = testsPlan(makeReview({ tests: { status: "timeout", run_all: true, reasons: ["Timed out."], tests_to_run: [], total: 0 } }));
    expect(plan.kind).toBe("run_all");
    expect(testsFigure(makeReview({ tests: { status: "timeout", run_all: true, tests_to_run: [], total: 0 } }), 9)).toEqual({
      figure: "All",
      runAll: true,
      total: 0,
    });
  });

  it("says no test needs to run only for a selection that found none", () => {
    expect(testsPlan(makeReview({ tests: { tests_to_run: [], total: 0, summary: "Only docs changed." } }))).toEqual({
      kind: "none",
      summary: "Only docs changed.",
    });
  });
});

describe("notMeasured", () => {
  it("lists nothing when every lane measured and the health check ran", () => {
    expect(notMeasured({ review: makeReview(), health: "done", notIndexed: 0 })).toEqual([]);
  });

  it("says why a change has no review, and never reads it as clean", () => {
    const lanes = notMeasured({ review: null, reviewUnavailable: "The change could not be scored: bad object.", health: "not_run", notIndexed: 0 });
    expect(lanes.map((l) => `${l.name}: ${l.state} (${l.reason ?? ""})`)).toEqual([
      "Change risk and test selection: not measured (The change could not be scored: bad object)",
      "Code health: not checked yet ()",
    ]);
  });

  it("names a health check that failed as not measured", () => {
    expect(notMeasured({ review: makeReview(), health: "error", notIndexed: 0 }).map((l) => l.state)).toEqual(["not measured"]);
  });

  it("names a hand-picked file set as not scored", () => {
    const [lane] = notMeasured({ review: undefined, health: "not_a_change", notIndexed: 0 });
    expect(lane?.reason).toMatch(/hand-picked file set has no diff to score/);
  });

  it("names every partial lane in order", () => {
    const review = makeReview({
      risk: { risk_percentile: null, review_priority: null, baseline_sample_size: 3, fix_history: { available: false, density: 0, percentile: null, files: [] } },
      tests: { status: "degraded", summary: "The index is behind the checkout." },
    });
    const lanes = notMeasured({ review, health: "unsupported", notIndexed: 2 });
    expect(lanes.map((l) => [l.key, l.state])).toEqual([
      ["rank", "not ranked"],
      ["fixes", "not measured"],
      ["tests", "partly measured"],
      ["index", "partly measured"],
      ["health", "not checked"],
    ]);
    expect(lanes[0]?.reason).toBe("3 scored commits in this repo, too few to rank against");
    expect(lanes[3]?.reason).toBe("2 changed files are new since the last index run");
  });

  it("gives a run-all verdict its first reason", () => {
    const lanes = notMeasured({
      review: makeReview({ tests: { status: "run_all", run_all: true, reasons: ["pyproject.toml changed", "other"] } }),
      health: "done",
      notIndexed: 0,
    });
    expect(lanes).toEqual([{ key: "tests", name: "Test selection", state: "could not narrow it", reason: "pyproject.toml changed" }]);
  });
});
