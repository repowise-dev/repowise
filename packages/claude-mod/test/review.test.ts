// Views over `get_change_risk` results recorded from the real server on an
// indexed copy of `requests` (one uncommitted edit per state; the overlap state
// adds a second local branch editing the same file). Error and timeout are
// client-side states, so they have no recorded payload.
import { describe, expect, it } from "vitest";
import { initialReview, isFileEdit, isRetryable, reduceReview, shouldReview, testsToRun, type ChangeRisk, type ReviewOutcome } from "../src/model/review";
import { initialSession, reduce } from "../src/model/session";
import { bandView } from "../src/views/band";
import { box, button, materialize, text, type Node } from "../src/views/elements";
import { DARK } from "@repowise-dev/ui/brand";
import { PRESS, directiveRows, reviewBandRow, reviewText, runTestsText, withCard } from "../src/views/review";
import { fixture } from "./fake-host";

const risk = (name: string): ChangeRisk => JSON.parse(fixture(`change-risk/${name}.json`)) as ChangeRisk;
const done = (name: string): ReviewOutcome => ({ phase: "done", risk: risk(name) });

/** Every string a tree draws, in order, with button labels as `hotkey: label`. */
function drawn(node: Node | null): string[] {
  if (node === null) return [];
  if (node.type === "Text") return node.children;
  if (node.type === "Button") return [`${node.props.hotkey}: ${node.props.label}`];
  if (node.type === "Raster") return [];
  return node.children.flatMap(drawn);
}

function colors(node: Node | null): string[] {
  if (node === null || node.type === "Button" || node.type === "Raster") return [];
  if (node.type === "Text") return node.props.color === undefined ? [] : [node.props.color];
  return node.children.flatMap(colors);
}

/** The card is one row of segments; split it back to compare them. */
const segments = (outcome: ReviewOutcome) => reviewText(outcome)?.split(" · ");

const TESTS = "Tests to run: 2 test files, inferred from the dependency graph, not measured: tests/test_requests.py, tests/test_utils.py";

describe("review model", () => {
  it("counts only edits that ran, made by Claude or its subagents", () => {
    const ran = { result: { type: "update" }, text: "ok" };
    expect(isFileEdit({ tool: "Edit", tool_use_id: "toolu_01" }, ran)).toBe(true);
    expect(isFileEdit({ tool: "Write", tool_use_id: "toolu_02" }, ran)).toBe(true);
    expect(isFileEdit({ tool: "MultiEdit" }, ran)).toBe(true);
    expect(isFileEdit({ tool: "NotebookEdit", tool_use_id: "toolu_03" }, ran)).toBe(true);
    expect(isFileEdit({ tool: "Read", tool_use_id: "toolu_04" }, ran)).toBe(false);
    expect(isFileEdit({ tool: "Bash", tool_use_id: "toolu_05" }, ran)).toBe(false);
    expect(isFileEdit({ tool: "Edit", tool_use_id: "toolu_plugin_06" }, ran)).toBe(false);
    expect(isFileEdit({ tool: "Edit", tool_use_id: "toolu_07" }, { deny: "no" })).toBe(false);
    expect(isFileEdit({ tool: "Edit", tool_use_id: "toolu_08" }, { isError: true, text: "old_string not found" })).toBe(false);
    expect(isFileEdit({ tool: "Edit", tool_use_id: "toolu_09" }, undefined)).toBe(false);
    expect(isFileEdit({}, ran)).toBe(false);
  });

  it("remembers an edit until the next turn starts, and a new turn drops the last review", () => {
    let s = reduceReview(initialReview, { type: "fileEdited" });
    expect(s.edited).toBe(true);
    expect(reduceReview(s, { type: "fileEdited" })).toBe(s);
    s = reduceReview(s, { type: "reviewStarted" });
    expect(s.outcome).toEqual({ phase: "reviewing" });
    s = reduceReview(s, { type: "reviewed", risk: risk("clear") });
    expect(s.outcome.phase).toBe("done");
    expect(reduceReview(s, { type: "turnStarted" })).toBe(initialReview);
    expect(reduceReview(initialReview, { type: "turnStarted" })).toBe(initialReview);
    const failed = reduceReview(s, { type: "reviewFailed", reason: "timeout", message: "x" });
    expect(failed.outcome).toEqual({ phase: "failed", reason: "timeout", message: "x" });
  });

  it("the session carries the review and leaves the rest of its state alone", () => {
    const s = reduce(initialSession, { type: "fileEdited" });
    expect(s.review.edited).toBe(true);
    expect({ ...s, review: initialReview }).toEqual(initialSession);
    expect(reduce(initialSession, { type: "turnStarted" })).toBe(initialSession);
    // A discovery keeps the review it found.
    expect(reduce(s, { type: "discovered", mode: "no-index", freshness: null }).review).toBe(s.review);
  });

  it("reviews only a finished main-loop turn that edited files", () => {
    expect(shouldReview({ reason: "answer" }, 1)).toBe(true);
    expect(shouldReview({}, 2)).toBe(true);
    expect(shouldReview({ reason: "answer" }, 0)).toBe(false);
    expect(shouldReview({ agentId: "sub" }, 1)).toBe(false);
    expect(shouldReview({ isAborted: true }, 1)).toBe(false);
    expect(shouldReview({ reason: "aborted" }, 1)).toBe(false);
    expect(shouldReview({ reason: "error" }, 1)).toBe(false);
  });

  it("retries a failed review once unless it timed out", () => {
    expect(isRetryable({ type: "reviewFailed", reason: "error" })).toBe(true);
    expect(isRetryable({ type: "reviewFailed", reason: "timeout" })).toBe(false);
    expect(isRetryable({ type: "reviewed" })).toBe(false);
  });

  it("reads the tests and their basis as the server sent them", () => {
    expect(testsToRun(risk("clear"))).toEqual({
      tests: ["tests/test_requests.py", "tests/test_utils.py"],
      total: 2,
      truncated: false,
      files: true,
      measured: false,
    });
    expect(testsToRun(risk("unavailable"))).toBeNull();
    expect(testsToRun(risk("nothing-to-score"))).toBeNull();
    const measured = { impacted_tests: { basis: "measured", tests_to_run: ["t::a"], tests_to_run_kind: "test_id" } } as ChangeRisk;
    expect(testsToRun(measured)).toEqual({ tests: ["t::a"], total: 1, truncated: false, files: false, measured: true });
  });
});

describe("review card beneath the answer", () => {
  it("says nothing before a review has a result", () => {
    expect(reviewText({ phase: "none" })).toBeNull();
    expect(reviewText({ phase: "reviewing" })).toBeNull();
  });

  it("clear with no other branch in these files collapses to one line", () => {
    expect(reviewText(done("clear"))).toBe("Change review (working tree) · health: no new findings in the 1 changed file");
  });

  it("a change that resolves findings and adds none says so in words", () => {
    expect(reviewText(done("resolved"))).toBe("Change review (working tree) · health: improved, 2 findings resolved, none new");
  });

  it("is one row: the engine stores a line break in this text as U+FFFD", () => {
    for (const name of ["clear", "findings", "overlap", "partial", "unavailable", "resolved"]) {
      expect(reviewText(done(name))).not.toMatch(/[\n\r]/);
    }
  });

  it("new findings lead, each with severity, reason and place", () => {
    expect(segments(done("findings"))).toEqual([
      "Change review (working tree, 1 changed file)",
      "Health: 2 new findings need review, starting with nested_complexity in src/requests/_internal_utils.py",
      "critical nested_complexity: classify_headers nests 7 levels deep (src/requests/_internal_utils.py:55)",
      "high complex_method: classify_headers has cyclomatic complexity 15 (src/requests/_internal_utils.py:55)",
      "Diff shape: bigger than 92% of this repo's recent commits; size, not danger",
      TESTS,
    ]);
  });

  it("another branch editing the same file keeps the full card, with a neutral mark", () => {
    expect(segments(done("overlap"))).toEqual([
      "Change review (working tree, 1 changed file)",
      "Health: no new findings in the 1 changed file",
      "Diff shape: bigger than 34% of this repo's recent commits; size, not danger",
      TESTS,
      "Branches: ◦ 1 other branch also edits src/requests/_internal_utils.py (fix-native-strings)",
    ]);
  });

  it("a partial comparison carries a scope line and is never read as clear", () => {
    expect(segments(done("partial"))).toEqual([
      "Change review (working tree, 2 changed files)",
      "Health: Nothing new in what was compared, but part of the change was not analysed",
      "Scope: compared 1 of 2 changed files; 1 not analysed (1 unsupported language), so this is not a clean bill",
      "Diff shape: bigger than 40% of this repo's recent commits; size, not danger",
      TESTS,
      "Branches: ◦ 2 other branches also edit tox.ini (origin/3.0, origin/proposed/3.0.0)",
    ]);
  });

  it("a change with nothing health can read says it was not compared", () => {
    expect(segments(done("unavailable"))).toEqual([
      "Change review (working tree, 1 changed file)",
      "Health: not compared: No changed file is health-analyzable, so nothing was compared",
      "Diff shape: bigger than 34% of this repo's recent commits; size, not danger",
      "Branches: ◦ 2 other branches also edit tox.ini (origin/3.0, origin/proposed/3.0.0)",
    ]);
  });

  it("an empty diff shows no card", () => {
    expect(reviewText(done("nothing-to-score"))).toBeNull();
  });

  it("an error and a timeout each say so in one line", () => {
    expect(reviewText({ phase: "failed", reason: "error", message: "repowise MCP server is not connected" })).toBe(
      "Change review could not run: repowise MCP server is not connected",
    );
    expect(reviewText({ phase: "failed", reason: "timeout", message: "get_change_risk timed out after 20000 ms" })).toBe(
      "Change review timed out after 20 s",
    );
    expect(reviewText({ phase: "done", risk: { error: "Could not read change 'HEAD': bad revision" } })).toBe(
      "Change review could not run: Could not read change 'HEAD': bad revision",
    );
  });

  it("a clean tree reviews HEAD and says the working tree had nothing", () => {
    const head = { ...risk("findings"), ref: "HEAD", working_tree: false };
    expect(segments({ phase: "done", risk: head })?.[0]).toBe(
      "Change review (HEAD (no uncommitted changes), 1 changed file)",
    );
  });

  it("counts findings past the ones the server listed, and resolved ones beside new ones", () => {
    const base = risk("findings");
    const more = { ...base, health_delta: { ...base.health_delta!, findings_total: 5, resolved: 1 } };
    const lines = segments({ phase: "done", risk: more })!;
    expect(lines[1]).toBe(
      "Health: 2 new findings need review, starting with nested_complexity in src/requests/_internal_utils.py; 1 finding resolved",
    );
    expect(lines).toContain("and 3 more (Details)");
  });

  it("an unranked diff and a capped test list say so", () => {
    const base = risk("clear");
    const capped = {
      ...base,
      risk_percentile: null,
      branch_overlap: risk("overlap").branch_overlap!,
      impacted_tests: { ...base.impacted_tests!, total: 23, truncated: true },
    };
    const lines = segments({ phase: "done", risk: capped })!;
    expect(lines).toContain("Diff shape: not ranked (no recent commits to compare); size, not danger");
    expect(lines).toContain(
      "Tests to run: 23 test files, inferred from the dependency graph, not measured (first 2 shown): tests/test_requests.py, tests/test_utils.py",
    );
    expect(runTestsText({ phase: "done", risk: capped })).toBe(
      "Run the tests Repowise names for this change (the first 2 of 23), inferred from the dependency graph, not measured: tests/test_requests.py tests/test_utils.py",
    );
  });

  it("a result without a directive says health was not reported", () => {
    expect(reviewText({ phase: "done", risk: { ref: "working tree", working_tree: true, risk_percentile: 10 } })).toBe(
      ["Change review (working tree)", "Health: not reported", "Diff shape: bigger than 10% of this repo's recent commits; size, not danger"].join(" · "),
    );
  });
});

describe("review band row", () => {
  it("shows the placeholder while the review runs", () => {
    expect(drawn(reviewBandRow({ phase: "reviewing" }, 100))).toEqual(["Reviewing the change..."]);
  });

  it("draws nothing without a result, for an empty diff, or on a failure", () => {
    expect(reviewBandRow({ phase: "none" }, 100)).toBeNull();
    expect(reviewBandRow(done("nothing-to-score"), 100)).toBeNull();
    expect(reviewBandRow({ phase: "failed", reason: "timeout", message: "" }, 100)).toBeNull();
    expect(reviewBandRow({ phase: "done", risk: { error: "boom" } }, 100)).toBeNull();
  });

  it.each([
    ["clear", ["review · health: no new findings", "1: Run tests", "3: Details"], []],
    ["resolved", ["review · health: improved, 2 resolved", "1: Run tests", "3: Details"], [DARK.success]],
    ["findings", ["review · health: 2 new findings, review required", "1: Run tests", "3: Details"], [DARK.error]],
    [
      "overlap",
      ["review · health: no new findings", " · ◦ 1 other branch edits these files", "1: Run tests", "3: Details"],
      [],
    ],
    [
      "partial",
      ["review · health: partly compared, no new findings", " · ◦ 2 other branches edit these files", "1: Run tests", "3: Details"],
      [],
    ],
    ["unavailable", ["review · health: not compared", " · ◦ 2 other branches edit these files", "3: Details"], []],
  ])("%s: health word in a health color only, then the buttons", (name, expected, expectedColors) => {
    const row = reviewBandRow(done(name), 120);
    expect(drawn(row)).toEqual(expected);
    expect(colors(row)).toEqual(expectedColors);
  });

  it("a clear review with no tests to run and no other branch leaves the band quiet", () => {
    const base = risk("clear");
    const quiet = { ...base, impacted_tests: { ...base.impacted_tests!, tests_to_run: [], total: 0 } };
    expect(reviewBandRow({ phase: "done", risk: quiet }, 120)).toBeNull();
    expect(reviewText({ phase: "done", risk: quiet })).toBe("Change review (working tree) · health: no new findings in the 1 changed file");
  });

  it("low-severity findings are amber", () => {
    const base = risk("findings");
    const low = { ...base, directive: { ...base.directive!, status: "review_recommended" as const } };
    const row = reviewBandRow({ phase: "done", risk: low }, 120);
    expect(drawn(row)[0]).toBe("review · health: 2 new findings, low severity");
    expect(colors(row)).toEqual([DARK.warning]);
  });

  it.each([60, 100, 180])("fits %i columns, dropping the overlap words before the buttons", (columns) => {
    const row = reviewBandRow(done("partial"), columns)!;
    const labels = drawn(row);
    expect(labels.slice(-2)).toEqual(["1: Run tests", "3: Details"]);
    const width = labels.reduce((n, s) => n + s.length, 0) + 2 * 2;
    expect(width).toBeLessThanOrEqual(columns);
  });

  it("joins the band under the hint, holding its place within two rows", () => {
    const state = {
      ...initialSession,
      mode: "lite" as const,
      hint: "no-server" as const,
      hintsShown: ["no-server" as const],
      freshness: { changedFiles: 3 },
      review: { edited: true, outcome: done("findings") },
    };
    const band = bandView(state, { columns: 120, hasSurvey: false });
    expect(drawn(band)).toEqual([
      "Lens map needs the local server: repowise serve --no-ui",
      "review · health: 2 new findings, review required",
      "1: Run tests",
      "3: Details",
    ]);
    expect(bandView({ ...initialSession, review: { edited: true, outcome: { phase: "reviewing" } } }, { columns: 80, hasSurvey: true })).toBeNull();
  });
});

describe("review buttons", () => {
  it("Details prints the whole directive", () => {
    expect(directiveRows(done("findings"))).toEqual([
      "Change review: review required",
      "2 new findings need review, starting with nested_complexity in src/requests/_internal_utils.py.",
      "Reasons:",
      "  critical defect: nested_complexity in classify_headers (added_lines)",
      "  high defect: complex_method in classify_headers (added_lines)",
      "Next actions:",
      "  Inspect src/requests/_internal_utils.py:55 (chf_7c35d63fcc13e52b)",
      "  Inspect src/requests/_internal_utils.py:55 (chf_1d478916e70baee8)",
      "  Run: tests/test_requests.py tests/test_utils.py",
    ]);
    expect(directiveRows(done("nothing-to-score"))).toBeNull();
    expect(directiveRows({ phase: "reviewing" })).toBeNull();
  });

  it("Run tests submits the tests with their basis in words", () => {
    expect(runTestsText(done("clear"))).toBe(
      "Run the tests Repowise names for this change, inferred from the dependency graph, not measured: tests/test_requests.py tests/test_utils.py",
    );
    const measured = { impacted_tests: { basis: "measured", tests_to_run: ["tests/a.py::t"], total: 1 } } as ChangeRisk;
    expect(runTestsText({ phase: "done", risk: measured })).toBe(
      "Run the tests Repowise names for this change, measured by stored coverage: tests/a.py::t",
    );
    expect(runTestsText(done("unavailable"))).toBeNull();
    expect(runTestsText({ phase: "none" })).toBeNull();
  });

  it("materializes buttons with the action their key names", () => {
    const pressed: string[] = [];
    const tree = box({ flexDirection: "row" }, [text("x"), button(PRESS.tests, "1", "Run tests")]);
    const table = {
      Box: (props: Record<string, unknown>) => props,
      Text: (props: Record<string, unknown>) => props,
      Button: (props: Record<string, unknown>) => props,
    };
    const built = materialize(tree, table, { [PRESS.tests]: () => pressed.push("tests") }) as {
      children: Array<{ onPress?: () => void; label?: string }>;
    };
    expect(built.children[1]?.label).toBe("Run tests");
    built.children[1]?.onPress?.();
    expect(pressed).toEqual(["tests"]);
    expect(() => materialize(tree, table)).toThrow("button lens-review-tests has no action");
  });
});

describe("card beneath the answer", () => {
  it("replaces the answer's text with the card, which the engine then shows beneath it", () => {
    const usage = { input_tokens: 1 };
    expect(withCard({ text: "done", usage }, "done", "Change review · x")).toEqual({ text: "Change review · x", usage });
  });

  it("keeps a line another hook already put beneath the answer", () => {
    expect(withCard({ text: "TL;DR" }, "done", "card")).toEqual({ text: "TL;DR · card" });
  });

  it("passes on a result it cannot read", () => {
    expect(withCard(undefined, "done", "card")).toBeUndefined();
    const odd = { other: 1 };
    expect(withCard(odd, "done", "card")).toBe(odd);
  });
});
