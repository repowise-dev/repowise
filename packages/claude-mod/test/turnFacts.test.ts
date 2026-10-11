// The turn dashboard's figures: what an edit reaches that Claude did not look
// at (over a blast radius recorded on an indexed Django copy,
// test/fixtures/flow/blast_query.json), the working set and how Claude came
// to each file, the bytes that filled its context, and the Repowise answers
// that came from the index before an edit.
import { describe, expect, it } from "vitest";
import { activityOf, initialFlow, reduceFlow, toolEnded, type FlowAction, type FlowState } from "../src/model/flow";
import {
  answeredBeforeEdit,
  beforeAccept,
  blastFacts,
  contextBytes,
  editedFiles,
  firstEditMs,
  openedKeys,
  testsRun,
  workingSet,
  type BlastResponse,
} from "../src/model/turnFacts";
import { fixture } from "./fake-host";

const ROOT = "C:\\work\\django";
const QUERY = "django/db/models/query.py";
const call = (tool: string, id: string, args: Record<string, unknown> = {}) => ({ tool, tool_use_id: id, ...args });
const start = (tool: string, id: string, at: number, args: Record<string, unknown> = {}): FlowAction => ({
  type: "toolStarted",
  activity: activityOf(call(tool, id, args), at, ROOT)!,
});
const end = (tool: string, id: string, at: number, outcome: unknown): FlowAction => toolEnded(call(tool, id), outcome, at, ROOT)!;
const run = (actions: FlowAction[], from: FlowState = initialFlow) => actions.reduce(reduceFlow, from);
const abs = (rel: string) => `${ROOT}\\${rel.replace(/\//g, "\\")}`;
const BLAST = JSON.parse(fixture("flow/blast_query.json")) as BlastResponse;
const facts = blastFacts(QUERY, BLAST);

/** get_context names query.py; Claude reads it and an importer, greps, edits query.py, runs one test folder, asks get_risk after the edit. */
function turn(): FlowState {
  return run([
    { type: "turnStarted", turnId: "t1", prompt: "edit", at: 1_000 },
    start("mcp__repowise__get_context", "c1", 2_000, { targets: [QUERY] }),
    end("mcp__repowise__get_context", "c1", 2_400, { text: fixture("flow/get_context.json") }),
    start("Read", "r1", 3_000, { file_path: abs(QUERY) }),
    end("Read", "r1", 3_100, { text: "x".repeat(1_000) }),
    start("Grep", "g1", 4_000, { pattern: "QuerySet" }),
    end("Grep", "g1", 4_100, { result: { filenames: ["django\\contrib\\contenttypes\\fields.py"] }, text: "y".repeat(100) }),
    start("Read", "r2", 5_000, { file_path: abs("django/contrib/contenttypes/fields.py") }),
    end("Read", "r2", 5_100, { text: "z".repeat(500) }),
    start("Read", "r3", 5_500, { file_path: abs("django/utils/tree.py") }),
    end("Read", "r3", 5_600, { text: "t".repeat(50) }),
    start("Edit", "e1", 9_000, { file_path: abs(QUERY), old_string: "a", new_string: "b" }),
    end("Edit", "e1", 9_100, { text: "ok" }),
    { type: "blastLanded", path: QUERY, facts },
    start("Bash", "b1", 10_000, { command: "python tests/runtests.py queries" }),
    end("Bash", "b1", 20_000, { text: "w".repeat(200) }),
    start("mcp__repowise__get_risk", "c2", 21_000, { targets: [QUERY] }),
    end("mcp__repowise__get_risk", "c2", 21_600, { text: fixture("flow/get_risk.json") }),
    start("mcp__repowise__get_why", "c3", 22_000, { query: "why" }),
    end("mcp__repowise__get_why", "c3", 22_500, { text: fixture("flow/get_why.json") }),
    { type: "turnEnded", at: 30_000, durationMs: 29_000, end: "answer" },
  ]);
}
const last = (s: FlowState) => s.turns.at(-1)!;

describe("blast radius facts", () => {
  it("reads direct importers, co-change partners strongest first, and the tests that reach the file, as the server gave them", () => {
    expect(facts.importers).toHaveLength(12);
    expect(facts.importers).not.toContain(QUERY);
    expect(facts.cochange[0]).toEqual({ path: "tests/queries/tests.py", score: 0.8024 });
    expect(facts.cochange.map((c) => c.score)).toEqual([...facts.cochange.map((c) => c.score)].sort((a, b) => b - a));
    expect(facts.tests).toMatchObject({ total: 17, basis: "inferred" });
    expect(facts.tests!.files).toHaveLength(17);
  });

  it("measured tests win over inferred; none, or no impact block, is null; another file's warnings are not this one's", () => {
    const measured: BlastResponse = { test_impact: { files: [{ source_file: "a.py", measured_tests: ["t.py"], measured_tests_total: 1, inferred_tests_total: 9 }] } };
    expect(blastFacts("a.py", measured).tests).toEqual({ total: 1, files: ["t.py"], basis: "measured" });
    expect(blastFacts("a.py", { test_impact: { files: [{ source_file: "a.py" }] } }).tests).toBeNull();
    expect(blastFacts("a.py", {})).toEqual({ importers: [], cochange: [], tests: null });
    expect(blastFacts("a.py", { cochange_warnings: [{ changed: "b.py", missing_partner: "c.py", score: 1 }] }).cochange).toEqual([]);
  });
});

describe("before you accept", () => {
  it("per edited file, only what Claude did not look at: importers not opened, co-change partners not opened, tests not run", () => {
    const s = turn();
    const [g] = beforeAccept(s, last(s));
    expect(g!.path).toBe(QUERY);
    expect(g!.importers).toEqual({ total: 12, opened: 1 });
    expect(g!.cochange.map((c) => c.path)).toEqual(["tests/queries/tests.py", "django/db/models/sql/query.py"]);
    // `runtests.py queries` names the tests/queries folder: its test files count as run.
    expect(g!.tests).toEqual({ total: 17, run: testsRun(s, facts.tests!.files), basis: "inferred" });
    expect(g!.tests!.run).toBeGreaterThan(0);
    expect(g!.tests!.run).toBeLessThan(17);
  });

  it("quiet when Claude looked at everything, or no blast radius landed", () => {
    const small = { importers: ["django/utils/tree.py"], cochange: [{ path: "django/utils/tree.py", score: 0.5 }], tests: null };
    const s = run([{ type: "blastLanded", path: QUERY, facts: small }], turn());
    expect(beforeAccept(s, last(s))).toEqual([]);
    const none = run([{ type: "turnStarted", turnId: "t", prompt: "", at: 0 }, start("Edit", "e", 1, { file_path: abs("a.py") }), end("Edit", "e", 2, {})]);
    expect(beforeAccept(none, last(none))).toEqual([]);
  });

  it("tests count as run only when a test command names them, as whole words, never by a generic name", () => {
    const s = (command: string) => run([start("Bash", "b", 1, { command })]);
    const tests = ["tests/queries/tests.py", "tests/async/test_async_queryset.py"];
    expect(testsRun(s("pytest tests/async/test_async_queryset.py"), tests)).toBe(1);
    expect(testsRun(s("python -m pytest -k test_async_queryset"), tests)).toBe(1);
    expect(testsRun(s("python runtests.py"), tests)).toBe(0);
    expect(testsRun(s("cat tests/async/test_async_queryset.py"), tests)).toBe(0);
    expect(testsRun(s("pytest test_async_querysets_more"), tests)).toBe(0);
    expect(testsRun(s("npm test -- queries async"), tests)).toBe(2);
  });

  it("edited files once each in the order edited; a failed edit is not one", () => {
    const s = run([start("Edit", "a", 1, { file_path: abs("b.py") }), start("Write", "b", 2, { file_path: abs("a.py") }), start("Edit", "c", 3, { file_path: abs("B.py") }), start("Edit", "d", 4, { file_path: abs("c.py") }), end("Edit", "d", 5, null)]);
    expect(editedFiles(last(s))).toEqual(["b.py", "a.py"]);
    expect(openedKeys(s)).toEqual(new Set(["b.py", "a.py", "c.py"]));
  });
});

describe("the working set", () => {
  it("edited first, then reads; each with how Claude came to it: the Repowise tool that named it, a search, or directly", () => {
    const s = turn();
    expect(workingSet(s, last(s))).toEqual([
      { path: QUERY, kind: "edited", via: { kind: "repowise", tool: "get_context" } },
      { path: "django/contrib/contenttypes/fields.py", kind: "read", via: { kind: "search" } },
      { path: "django/utils/tree.py", kind: "read", via: { kind: "direct" } },
    ]);
  });
});

describe("context and timing", () => {
  it("bytes of each kind of result Claude received this turn, and when it first edited", () => {
    const s = turn();
    const t = last(s);
    const rw = t.activities.filter((a) => a.kind === "repowise").reduce((n, a) => n + (a.bytes ?? 0), 0);
    expect(contextBytes(t)).toEqual({ repowise: rw, read: 1_550, search: 100, shell: 200, other: 2 });
    expect(firstEditMs(t)).toBe(8_000);
    expect(firstEditMs({ ...t, activities: [] })).toBeNull();
  });

  it("a Repowise call about a file Claude had already edited came from the index before that edit; the others did not", () => {
    expect(answeredBeforeEdit(turn())).toEqual(new Set(["c2"]));
  });
});
