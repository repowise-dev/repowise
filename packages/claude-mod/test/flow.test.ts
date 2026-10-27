// Flow's model: the activity record, the user's words, bytes per result, "found via",
// the ring caps, and per-tool reply summaries over replies recorded from
// `repowise mcp` on an indexed Django copy (test/fixtures/flow).
import { describe, expect, it } from "vitest";
import {
  MAX_ACTIVITIES,
  MAX_TURNS,
  activities,
  activityOf,
  inFlight,
  initialFlow,
  isWorking,
  openable,
  pathKey,
  reduceFlow,
  repowiseTool,
  searchHits,
  shortPath,
  toolEnded,
  turnEnd,
  turnTotals,
  userWords,
  visits,
  type FlowAction,
  type FlowState,
} from "../src/model/flow";
import { EXCERPT_CHARS, PARSE_CHARS, summarizeReply } from "../src/model/replies";

/** A reply body as the server sends it. */
const body = (tool: string, result: unknown) => summarizeReply(tool, JSON.stringify({ result }));
import { fixture } from "./fake-host";

const ROOT = "C:\\work\\django";
const reply = (tool: string) => fixture(`flow/${tool}.json`);
const run = (actions: FlowAction[], from: FlowState = initialFlow) => actions.reduce(reduceFlow, from);

const call = (tool: string, id: string, args: Record<string, unknown> = {}) => ({ tool, tool_use_id: id, ...args });
function start(tool: string, id: string, at: number, args: Record<string, unknown> = {}): FlowAction {
  return { type: "toolStarted", activity: activityOf(call(tool, id, args), at, ROOT)! };
}
function end(tool: string, id: string, at: number, outcome: unknown): FlowAction {
  return toolEnded(call(tool, id), outcome, at, ROOT)!;
}

describe("reply summaries over recorded replies", () => {
  it("get_context: targets, documentation, symbols shown of total, hotspots; the index commit and budget", () => {
    const s = summarizeReply("get_context", reply("get_context"));
    expect(s.inside).toEqual([
      { what: "targets", n: 1 },
      { what: "docs", n: 1 },
      { what: "symbols", n: 40, of: 180 },
      { what: "hotspots", n: 1 },
    ]);
    expect(s.behind).toMatchObject({ commit: "e78991410b78", ageDays: 0, complete: true, budget: { used: 7566, limit: 24000 }, semantic: false });
    expect(s.paths).toEqual(["django/db/models/query.py"]);
    expect(s.bytes).toBe(new TextEncoder().encode(reply("get_context")).length);
    expect(s.excerpt).toHaveLength(EXCERPT_CHARS);
    expect(s.error).toBeNull();
  });

  it("get_risk: dependents, co-change partners; capped and tokens left out", () => {
    const s = summarizeReply("get_risk", reply("get_risk"));
    expect(s.inside).toEqual([
      { what: "targets", n: 1 },
      { what: "dependents", n: 12 },
      { what: "coChange", n: 25 },
      { what: "hotspots", n: 1 },
    ]);
    expect(s.behind).toMatchObject({ complete: false, omittedTokens: 1849 });
    expect(s.paths).toContain("tests/queries/tests.py");
  });

  it("get_answer: citations, bodies, rationale shown of total, guesses; confidence, grounding, retrieval, why degraded", () => {
    const s = summarizeReply("get_answer", reply("get_answer"));
    expect(s.inside).toEqual([
      { what: "citations", n: 1 },
      { what: "bodies", n: 1 },
      { what: "rationale", n: 2, of: 6 },
      { what: "guesses", n: 3 },
    ]);
    expect(s.behind).toMatchObject({ confidence: "medium", grounding: "symbol_body", retrieval: "high", degraded: "no-llm-provider" });
  });

  it("get_why, search_codebase, get_symbol: their own evidence", () => {
    expect(summarizeReply("get_why", reply("get_why")).inside).toEqual([
      { what: "decisions", n: 0 },
      { what: "rationale", n: 3 },
    ]);
    expect(summarizeReply("get_why", reply("get_why")).paths).toEqual(["django/forms/models.py", "django/core/paginator.py", "django/db/models/manager.py"]);
    expect(summarizeReply("search_codebase", reply("search_codebase")).inside).toEqual([{ what: "results", n: 5 }]);
    const symbol = summarizeReply("get_symbol", reply("get_symbol"));
    expect(symbol.inside).toEqual([{ what: "lines", n: 600 }]);
    expect(symbol.behind).toMatchObject({ verified: true, omittedTokens: 7089 });
  });

  it("an unknown tool falls back to the reply's own lists; a known tool with nothing it reads does too", () => {
    const text = JSON.stringify({ result: { findings: [1, 2], files: ["a.py"], _private: [1], empty: [], name: "x" } });
    expect(summarizeReply("get_dead_code", text).inside).toEqual([
      { what: "findings", n: 2 },
      { what: "files", n: 1 },
    ]);
    expect(summarizeReply("search_codebase", JSON.stringify({ result: { hits: [1] } })).inside).toEqual([{ what: "hits", n: 1 }]);
  });

  it("_meta beside the result is read; an error is said", () => {
    const beside = summarizeReply("get_context", JSON.stringify({ result: { targets: {} }, _meta: { indexed_commit: "abc", index_age_days: 3 } }));
    expect(beside.behind).toMatchObject({ commit: "abc", ageDays: 3 });
    expect(summarizeReply("get_context", JSON.stringify({ error: { message: "no index" } })).error).toBe("no index");
    expect(summarizeReply("get_context", JSON.stringify({ result: { error: "bad target\nmore" } })).error).toBe("bad target");
    expect(summarizeReply("get_context", JSON.stringify({ result: { _meta: { completeness: { capped: true } } } })).behind.complete).toBe(false);
  });

  it("text that is not JSON keeps its first line as the error; text too large to parse keeps its size only", () => {
    const plain = summarizeReply("get_why", "Error: tool failed\nstack");
    expect(plain).toMatchObject({ parsed: false, error: "Error: tool failed", inside: [], paths: [] });
    expect(summarizeReply("get_why", "   ").error).toBeNull();
    const big = summarizeReply("get_symbol", JSON.stringify({ result: { source: "x".repeat(PARSE_CHARS) } }));
    expect(big).toMatchObject({ parsed: false, error: null });
    expect(big.bytes).toBeGreaterThan(PARSE_CHARS);
    expect(summarizeReply("get_why", "[1]")).toMatchObject({ parsed: false });
  });

  it("get_why's commits from the archaeology; a result that is not an object reads as empty", () => {
    const s = body("get_why", { decisions: [], code_rationale: [{ path: "a\\b.py" }], git_archaeology: { file_commits: [1, 2] } });
    expect(s).toMatchObject({ paths: ["a/b.py"], inside: [{ what: "decisions", n: 0 }, { what: "rationale", n: 1 }, { what: "commits", n: 2 }] });
    expect(body("get_context", null).inside).toEqual([{ what: "targets", n: 0 }]);
  });

  it("the path walk takes cited files and target keys, skips _meta, and stops at its depth", () => {
    const deep = { a: { b: { c: { d: { e: { f: { g: { path: "too/deep.py" } } } } } } } };
    const s = body("x", { citations: ["c.py", 3], targets: { "t.py": {} }, _meta: { path: "meta.py" }, deep });
    expect(s.paths).toEqual(["c.py", "t.py"]);
  });
});

describe("activities", () => {
  it("names Repowise tools from its three server name forms only; everything else by its own name", () => {
    expect(repowiseTool("mcp__plugin_repowise_repowise__get_context")).toBe("get_context");
    expect(repowiseTool("mcp__repowise__get_answer")).toBe("get_answer");
    expect(repowiseTool("mcp__claude_ai_Repowise__get_why")).toBe("get_why");
    expect(repowiseTool("MCP__REPOWISE__get_risk")).toBe("get_risk");
    expect(repowiseTool("mcp__github__get_issue")).toBeNull();
    expect(repowiseTool("mcp__not_repowise_fork__get_context")).toBeNull();
    expect(repowiseTool("mcp__repowise_clone__get_context")).toBeNull();
    expect(repowiseTool("Read")).toBeNull();
  });

  it("a turn's end from turn.complete: an answer, stopped when aborted, did not finish on a refusal or an error", () => {
    expect(turnEnd("answer", false)).toBe("answer");
    expect(turnEnd("aborted", true)).toBe("stopped");
    expect(turnEnd("refusal", false)).toBe("failed");
    expect(turnEnd("error", false)).toBe("failed");
    expect(turnEnd(undefined, true)).toBe("stopped");
    expect(turnEnd(undefined, undefined)).toBe("answer");
  });

  it("compacts arguments and shows paths relative to the session's directory", () => {
    const a = activityOf(call("Read", "t1", { file_path: `${ROOT}\\django\\db\\models\\query.py`, limit: 40 }), 5, ROOT)!;
    expect(a).toMatchObject({ kind: "read", tool: "Read", paths: ["django/db/models/query.py"], args: [["file_path", "django/db/models/query.py"]] });
    const rw = activityOf(call("mcp__repowise__get_context", "t2", { targets: ["query.py", "base.py"], include: ["callers"] }), 5, ROOT)!;
    expect(rw).toMatchObject({ kind: "repowise", tool: "get_context", args: [["targets", "query.py, base.py"]] });
    const other = activityOf(call("Bash", "t3", { command: "npm   test", agentId: "sub1", timeout: { s: 5 } }), 5, null)!;
    expect(other).toMatchObject({ kind: "shell", agentId: "sub1", args: [["command", "npm test"]], command: "npm   test", asked: [] });
    expect(activityOf(call("WebFetch", "w", { url: "https://x" }), 0, null)).toMatchObject({ kind: "other", command: null });
    // Symbol ids in targets are not files.
    expect(activityOf(call("mcp__repowise__get_context", "s", { targets: ["a.py::f", `${ROOT}\\b.py`] }), 0, ROOT)!.asked).toEqual(["b.py"]);
    expect(activityOf(call("Task", "t4", { description: { long: true } }), 0, null)!.args).toEqual([["description", '{"long":true}']]);
    expect(shortPath("/elsewhere/x.py", "/work/app/")).toBe("/elsewhere/x.py");
    expect(shortPath("C:\\a\\b.py", null)).toBe("C:/a/b.py");
  });

  it("an edit's size from its arguments; Lens's own calls and malformed ones are not activities", () => {
    expect(activityOf(call("Edit", "e", { file_path: "/w/a.py", old_string: "a", new_string: "a\nb" }), 0, "/w")!.lines).toEqual({ delta: 1 });
    expect(activityOf(call("Write", "w", { file_path: "/w/a.py", content: "a\nb\nc" }), 0, "/w")!.lines).toEqual({ written: 3 });
    expect(activityOf(call("NotebookEdit", "n", { notebook_path: "/w/a.ipynb" }), 0, "/w")).toMatchObject({ kind: "edit", lines: null, paths: ["a.ipynb"] });
    expect(activityOf(call("mcp__repowise__get_context", "toolu_plugin_1"), 0, null)).toBeNull();
    expect(activityOf({ tool: "Read" } as never, 0, null)).toBeNull();
    expect(toolEnded(call("Read", "toolu_plugin_2"), {}, 0, null)).toBeNull();
  });

  it("a search's hits: count and names, or the count alone, or nothing", () => {
    expect(searchHits({ result: { filenames: ["django\\db\\a.py", 3], numFiles: 2 } }, ROOT)).toEqual({ hits: 1, hitPaths: ["django/db/a.py"] });
    expect(searchHits({ result: { numFiles: 4 } }, ROOT)).toEqual({ hits: 4, hitPaths: [] });
    expect(searchHits({ result: {} }, ROOT)).toEqual({ hits: null, hitPaths: [] });
    expect(searchHits(null, ROOT)).toEqual({ hits: null, hitPaths: [] });
    expect(searchHits({ result: "text" }, ROOT)).toEqual({ hits: null, hitPaths: [] });
  });

  it("an ending: failure when next(e) threw or said so; a Repowise reply summarized from its text", () => {
    expect(end("Read", "r", 9, null)).toMatchObject({ isError: true, reply: null });
    expect(end("Read", "r", 9, { isError: true })).toMatchObject({ isError: true });
    expect(end("Read", "r", 9, undefined)).toMatchObject({ isError: false });
    expect(end("mcp__repowise__get_risk", "r", 9, { text: reply("get_risk") })).toMatchObject({ reply: { tool: "get_risk" } });
    expect(end("mcp__repowise__get_risk", "r", 9, { result: {} })).toMatchObject({ reply: null });
  });
});

/** A turn: get_context names query.py, then Claude reads it and edits it, reads another, and greps. */
function session(): FlowState {
  return run([
    { type: "turnStarted", turnId: "t-1", prompt: "add a comment above bulk_create", at: 1_000 },
    start("mcp__repowise__get_context", "c1", 2_000, { targets: ["django/db/models/query.py"] }),
    end("mcp__repowise__get_context", "c1", 2_420, { text: reply("get_context") }),
    start("Read", "r1", 3_000, { file_path: `${ROOT}\\django\\db\\models\\query.py` }),
    end("Read", "r1", 3_300, {}),
    start("Read", "r2", 4_000, { file_path: `${ROOT}\\django\\db\\models\\base.py` }),
    end("Read", "r2", 4_100, {}),
    start("Grep", "g1", 4_500, { pattern: "bulk_create" }),
    end("Grep", "g1", 4_600, { result: { filenames: ["django\\db\\models\\query.py", "tests\\bulk_create\\tests.py"] } }),
    start("Edit", "e1", 5_000, { file_path: `${ROOT}\\django\\db\\models\\query.py`, old_string: "a", new_string: "# c\na" }),
    end("Edit", "e1", 5_200, {}),
    { type: "turnEnded", at: 9_000, durationMs: 8_000, end: "answer" },
  ]);
}

describe("the user's own words", () => {
  it("drops injected blocks at the start of a turn's text; nothing typed leaves nothing", () => {
    const handBack = '<agent-message from="agent-7">[Subagent hand-back] The text below is the report.</agent-message>\nfix the bulk_create comment';
    expect(userWords(handBack)).toBe("fix the bulk_create comment");
    expect(userWords('<\\agent-message from=\\"x\\"> [Subagent hand-back] The text below i')).toBe("");
    expect(userWords("<system-reminder>be brief</system-reminder><task-notification>done</task-notification>  hi")).toBe("hi");
    expect(userWords("<command-name>/lens</command-name><command-args></command-args>")).toBe("");
    expect(userWords("why does <b>this</b> render?")).toBe("why does <b>this</b> render?");
    // Trailing and inline blocks go too, and the words either side keep one space.
    expect(userWords("fix the bulk_create comment\n<system-reminder>be brief</system-reminder>")).toBe("fix the bulk_create comment");
    expect(userWords("fix <task-notification>done</task-notification> the comment")).toBe("fix the comment");
    expect(userWords("fix it <system-reminder>never closed")).toBe("fix it");
    const s = reduceFlow(initialFlow, { type: "turnStarted", turnId: "t", prompt: handBack, at: 0 });
    expect(s.turns[0]!.prompt).toBe("fix the bulk_create comment");
  });
});

describe("the flow reducer", () => {
  it("a result's bytes are counted from its text, Repowise's from the reply; none without text", () => {
    expect(end("Read", "r", 1, { text: "héllo" })).toMatchObject({ bytes: 6 });
    expect(end("Read", "r", 1, { result: {} })).toMatchObject({ bytes: null });
  });

  it("records the turn in order, with the reply, the bytes each result carried, and the files Repowise named first", () => {
    const s = session();
    expect(s.turns).toHaveLength(1);
    const [t] = s.turns;
    expect(t).toMatchObject({ seq: 1, turnId: "t-1", prompt: "add a comment above bulk_create", endedAt: 9_000, durationMs: 8_000 });
    expect(t!.activities.map((a) => [a.kind, a.tool])).toEqual([
      ["repowise", "get_context"],
      ["read", "Read"],
      ["read", "Read"],
      ["search", "Grep"],
      ["edit", "Edit"],
    ]);
    const [rw, read, other] = t!.activities;
    expect(rw!.bytes).toBe(new TextEncoder().encode(reply("get_context")).length);
    expect(rw!.asked).toEqual(["django/db/models/query.py"]);
    expect(rw!.reply?.inside[0]).toEqual({ what: "targets", n: 1 });
    expect(read!.namedBy).toEqual({ id: "c1", tool: "get_context", at: 2_420, turn: 1 });
    expect(other!.namedBy).toBeNull();
    expect(t!.end).toBe("answer");
    expect(turnTotals(t!)).toEqual({ repowise: 1, answered: 1, answeredMs: 420, reads: 2, edits: 1, opened: 2, named: 1, editNamed: true });
    expect(isWorking(s)).toBe(false);
  });

  it("visits: reads and edits numbered in order, a search's hits share one number", () => {
    expect(visits(session()).map((v) => [v.path, v.kind, v.order])).toEqual([
      ["django/db/models/query.py", "read", 1],
      ["django/db/models/base.py", "read", 2],
      ["django/db/models/query.py", "search-hit", 3],
      ["tests/bulk_create/tests.py", "search-hit", 3],
      ["django/db/models/query.py", "edit", 4],
    ]);
    const failed = run([start("Read", "x", 1, { file_path: "/w/a.py" }), end("Read", "x", 2, null), start("Grep", "g", 3), end("Grep", "g", 4, { result: { mode: "content" } })]);
    expect(visits(failed)).toEqual([]);
    expect(activities(session())).toHaveLength(5);
  });

  it("a call before any turn starts one Lens did not see begin; an end for an unknown call changes nothing", () => {
    const s = run([start("Read", "r", 50, { file_path: "/a.py" })]);
    expect(s.turns[0]).toMatchObject({ seq: 1, turnId: null, prompt: "", startedAt: 50, endedAt: null });
    expect(isWorking(s)).toBe(true);
    expect(reduceFlow(s, end("Read", "zzz", 60, {}))).toBe(s);
    expect(reduceFlow(initialFlow, { type: "turnEnded", at: 1, durationMs: null, end: "answer" })).toBe(initialFlow);
    const ended = reduceFlow(s, { type: "turnEnded", at: 80, durationMs: null, end: "answer" });
    expect(ended.turns[0]!.durationMs).toBe(30);
    expect(reduceFlow(ended, { type: "turnEnded", at: 99, durationMs: 1, end: "answer" })).toBe(ended);
  });

  it("keeps six turns and folds older ones into totals; keeps 60 activities a turn and counts the rest", () => {
    let s = initialFlow;
    for (let i = 0; i < MAX_TURNS + 2; i++) s = run([{ type: "turnStarted", turnId: `t${i}`, prompt: "p", at: i }, start("mcp__repowise__get_why", `w${i}`, i), start("Read", `r${i}`, i, { file_path: `/f${i}.py` }), { type: "turnEnded", at: i + 1, durationMs: 1, end: "answer" }], s);
    expect(s.turns.map((t) => t.seq)).toEqual([3, 4, 5, 6, 7, 8]);
    expect(s.earlier).toEqual({ turns: 2, repowise: 2, reads: 2, edits: 0 });
    let big = run([{ type: "turnStarted", turnId: "x", prompt: "", at: 0 }]);
    for (let i = 0; i < MAX_ACTIVITIES + 5; i++) big = reduceFlow(big, start("Bash", `b${i}`, i));
    expect(big.turns[0]!.activities).toHaveLength(MAX_ACTIVITIES);
    expect(big.turns[0]!.dropped).toBe(5);
  });

  it("open rows toggle; j and k step through the Repowise calls", () => {
    const s = run([start("mcp__repowise__get_why", "a", 1), start("Read", "r", 2, { file_path: "/x" }), start("mcp__repowise__get_risk", "b", 3)]);
    expect(openable(s)).toEqual(["a", "b"]);
    expect(reduceFlow(s, { type: "step", by: 1 }).open).toBe("a");
    expect(reduceFlow(s, { type: "step", by: -1 }).open).toBe("b");
    const onA = reduceFlow(s, { type: "toggle", id: "a" });
    expect(onA.open).toBe("a");
    expect(reduceFlow(onA, { type: "toggle", id: "a" }).open).toBeNull();
    expect(reduceFlow(onA, { type: "step", by: 1 }).open).toBe("b");
    expect(reduceFlow(onA, { type: "step", by: -1 })).toBe(onA);
    expect(reduceFlow(initialFlow, { type: "step", by: 1 })).toBe(initialFlow);
  });

  it("path keys fold separators and case; a reply naming a path twice keeps its first naming", () => {
    expect(pathKey(".\\Django\\DB\\query.py")).toBe("django/db/query.py");
    const twice = run([
      start("mcp__repowise__get_context", "a", 1),
      end("mcp__repowise__get_context", "a", 2, { text: reply("get_context") }),
      start("mcp__repowise__get_risk", "b", 3),
      end("mcp__repowise__get_risk", "b", 4, { text: reply("get_risk") }),
      start("mcp__repowise__get_risk", "c", 5),
      end("mcp__repowise__get_risk", "c", 6, { text: "not json" }),
    ]);
    expect(twice.named["django/db/models/query.py"]).toEqual({ id: "a", tool: "get_context", at: 2, turn: 1 });
    expect(twice.named["tests/queries/tests.py"]).toEqual({ id: "b", tool: "get_risk", at: 4, turn: 1 });
  });

  it("named first only on an exact repo-relative match; an absolute reply path under the root is made relative", () => {
    const abs = JSON.stringify({ result: { targets: { [`${ROOT}\\django\\db\\a.py`]: {} }, file: "pkg/b.py" } });
    const s = run([
      start("mcp__repowise__get_context", "a", 1),
      end("mcp__repowise__get_context", "a", 2, { text: abs }),
      start("Read", "r1", 3, { file_path: `${ROOT}\\django\\db\\a.py` }),
      // Same tail, other place: not the file the reply named.
      start("Read", "r2", 4, { file_path: `${ROOT}\\vendor\\pkg\\b.py` }),
      start("Read", "r3", 5, { file_path: `${ROOT}\\pkg\\b.py` }),
      start("Read", "r4", 6, { file_path: "D:\\other\\pkg\\b.py" }),
    ]);
    expect(s.turns[0]!.activities.slice(1).map((a) => a.namedBy?.id ?? null)).toEqual(["a", null, "a", null]);
  });

  it("a prompt while the last turn is open closes it as stopped; turn.complete says how a turn ended", () => {
    const s = run([
      { type: "turnStarted", turnId: "t1", prompt: "one", at: 0 },
      { type: "turnStarted", turnId: "t2", prompt: "two", at: 5_000 },
      { type: "turnEnded", at: 6_000, durationMs: null, end: "failed" },
    ]);
    expect(s.turns.map((t) => [t.seq, t.end, t.durationMs])).toEqual([
      [1, "stopped", 5_000],
      [2, "failed", 1_000],
    ]);
  });
});
