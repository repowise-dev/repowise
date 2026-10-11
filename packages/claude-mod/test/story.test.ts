// The map's view of the current turn, read from Flow's record (built here
// with Flow's own reducer from the recorded Django calls' shapes) and, with
// Flow off, from the map's trail.
import { describe, expect, it } from "vitest";
import { activityOf, initialFlow, reduceFlow, toolEnded, type FlowState } from "../src/model/flow";
import { combineCallers, litFromFlow, litFromTrail, storyFromFlow, storyFromTrail, storyTurn, withImporters } from "../src/model/story";
import type { Callers } from "../src/model/trail";
import { NO_LIT } from "../src/views/overlay";
import { initialTrail, reduceTrail } from "../src/model/trail";
import { importers } from "./django";

const ROOT = "C:\\work\\django";
const QUERY = "django/db/models/query.py";
let n = 0;

type Step = "turn" | { tool: string; file?: string; pattern?: string; hits?: string[]; edit?: [string, string] };

/** The call `tool.call` would see for a step. */
function callOf(step: Exclude<Step, "turn">) {
  return {
    tool: step.tool,
    tool_use_id: `toolu_${n++}`,
    ...(step.file === undefined ? {} : { file_path: `${ROOT}\\${step.file.replace(/\//g, "\\")}` }),
    ...(step.pattern === undefined ? {} : { pattern: step.pattern }),
    ...(step.edit === undefined ? {} : { old_string: step.edit[0], new_string: step.edit[1] }),
  };
}

function nextTurn(s: FlowState): FlowState {
  const ended = reduceFlow(s, { type: "turnEnded", at: n, durationMs: 1, end: "answer" });
  return reduceFlow(ended, { type: "turnStarted", turnId: null, prompt: "", at: n++ });
}

/** One call, started and ended as Flow records it. */
function called(s: FlowState, step: Exclude<Step, "turn">): FlowState {
  const e = callOf(step);
  const activity = activityOf(e, n, ROOT);
  const started = activity === null ? s : reduceFlow(s, { type: "toolStarted", activity });
  const ended = toolEnded(e, { result: step.hits === undefined ? {} : { filenames: step.hits } }, n, ROOT);
  return ended === null ? started : reduceFlow(started, ended);
}

/** Flow after these calls, a new turn at each "turn". */
function flow(steps: Step[]): FlowState {
  return steps.reduce((s, step) => (step === "turn" ? nextTurn(s) : called(s, step)), initialFlow);
}

/** The recorded Django turn: two searches, two files opened, query.py edited by one line. */
const django = flow([
  { tool: "Read", file: "setup.py" },
  "turn",
  { tool: "Grep", pattern: "class QuerySet", hits: ["django\\db\\models\\query.py", "tests\\queries\\tests.py"] },
  { tool: "Glob", pattern: "django/db/models/sql/*.py", hits: ["django\\db\\models\\sql\\query.py"] },
  { tool: "Read", file: "django/db/models/manager.py" },
  { tool: "Read", file: QUERY },
  { tool: "Edit", file: QUERY, edit: ["a", "a\nb"] },
]);

describe("from Flow's record", () => {
  it("the story is the latest turn that touched a file: searches with counts, files opened once, edits with their size", () => {
    expect(storyFromFlow(django)).toEqual({
      searched: [
        { pattern: "class QuerySet", hits: 2 },
        { pattern: "django/db/models/sql/*.py", hits: 1 },
      ],
      opened: [
        { name: "manager.py", namedBy: null },
        { name: "query.py", namedBy: null },
      ],
      edited: [{ name: "query.py", lines: { delta: 1 } }],
    });
  });

  it("a new turn with no file yet keeps the last one that had any; nothing before any", () => {
    const later = flow([{ tool: "Read", file: "a.py" }, "turn", { tool: "Bash" }]);
    expect(storyTurn(later)?.seq).toBe(1);
    expect(storyFromFlow(initialFlow)).toEqual({ searched: [], opened: [], edited: [] });
    expect(litFromFlow(initialFlow)).toMatchObject({ reads: [], edit: null });
  });

  it("what the turn lit: matches, opened files, the edit and the current step, repo-relative", () => {
    expect(litFromFlow(django)).toEqual({
      hits: [QUERY, "tests/queries/tests.py", "django/db/models/sql/query.py"],
      reads: ["django/db/models/manager.py", QUERY],
      named: [],
      importers: [],
      edits: [QUERY],
      edit: QUERY,
      current: QUERY,
    });
  });

  it("files a Repowise reply named this turn are lit, and the opened row says which tool named one", () => {
    const turn = storyTurn(django)!;
    const naming = { id: "toolu_rw", tool: "get_context", at: 1, turn: turn.seq };
    const named: FlowState = {
      ...django,
      named: { [QUERY]: naming, "old/file.py": { ...naming, turn: turn.seq - 1 } },
      turns: django.turns.map((t) => (t !== turn ? t : { ...t, activities: t.activities.map((a) => (a.paths[0] === QUERY ? { ...a, namedBy: naming } : a)) })),
    };
    expect(litFromFlow(named).named).toEqual([QUERY]);
    expect(storyFromFlow(named).opened[1]).toEqual({ name: "query.py", namedBy: "get_context" });
  });
});

describe("with Flow off, from the map's trail", () => {
  let t = reduceTrail(initialTrail, { type: "read", path: "c:/work/django/django/db/models/manager.py" });
  t = reduceTrail(t, { type: "search", paths: ["c:/work/django/tests/queries/tests.py", "c:/elsewhere/x.py"] });
  t = reduceTrail(t, { type: "edit", path: "c:/work/django/django/db/models/query.py" });

  it("lights the trail's files relative to the repo, dropping any outside it", () => {
    expect(litFromTrail(t, ROOT, true)).toEqual({
      hits: ["tests/queries/tests.py"],
      reads: ["django/db/models/manager.py", QUERY],
      named: [],
      importers: [],
      edits: [QUERY],
      edit: QUERY,
      current: QUERY,
    });
  });

  it("tells the story without patterns or sizes, which only Flow records", () => {
    expect(storyFromTrail(t)).toEqual({
      searched: [{ pattern: "", hits: 2 }],
      opened: [
        { name: "manager.py", namedBy: null },
        { name: "query.py", namedBy: null },
      ],
      edited: [{ name: "query.py", lines: null }],
    });
    expect(storyFromTrail(initialTrail)).toEqual({ searched: [], opened: [], edited: [] });
  });
});

describe("withImporters", () => {
  const lit = litFromFlow(django);
  const asked = (answers: Record<string, Callers>) => (p: string) => answers[p] ?? null;

  it("joins the importers of the turn's edit, with what it reaches", () => {
    const { lit: joined, reach } = withImporters(lit, asked({ [QUERY]: { status: "ready", paths: importers } }));
    expect(joined.importers).toEqual(importers);
    expect(reach).toMatchObject({ edit: QUERY, callers: { status: "ready" } });
    expect(reach?.opened.has("django/db/models/manager.py")).toBe(true);
  });

  it("while the lookup runs: no importers yet, the reach says loading; nothing asked, no reach", () => {
    expect(withImporters(lit, asked({ [QUERY]: { status: "loading" } }))).toMatchObject({ lit: { importers: [] }, reach: { callers: { status: "loading" } } });
    expect(withImporters(lit, asked({ "other.py": { status: "ready", paths: ["x"] } })).reach).toBeNull();
    expect(withImporters(NO_LIT, asked({})).reach).toBeNull();
  });

  it("several edits: their importers as one set, the edited files themselves left out", () => {
    const two = { ...lit, edits: ["a.py", "b.py"], edit: "b.py" };
    const joined = withImporters(two, asked({ "a.py": { status: "ready", paths: ["x.py", "b.py"] }, "b.py": { status: "ready", paths: ["x.py", "y.py"] } }));
    expect(joined.lit.importers).toEqual(["x.py", "y.py"]);
    expect(combineCallers([{ status: "failed" }], new Set())).toEqual({ status: "failed" });
    expect(combineCallers([{ status: "failed" }, { status: "loading" }], new Set())).toEqual({ status: "loading" });
    expect(combineCallers([], new Set())).toBeNull();
  });
});
