// The pane's Ask and Recap tabs and the compaction brief, over replies
// recorded from `repowise mcp` 0.54.0 on indexed copies of Django and
// requests (test/fixtures/ask, cut to the fields Lens reads).
import { describe, expect, it } from "vitest";
import { askReply, askRoute, lensCommand, mayHaveUsedModel, whyDraft, type AskReply } from "../src/model/ask";
import type { ChangeRisk } from "../src/model/review";
import { initialSession, reduce, surfacedDecisions, type SessionAction, type SessionState } from "../src/model/session";
import { replyMarkdown } from "../src/views/answer";
import { bandView } from "../src/views/band";
import { BRIEF_PRESS, briefBandRow, briefText, listWithin } from "../src/views/brief";
import { ASK_CHARS, BRIEF_CHARS, savingsLine } from "../src/views/copy";
import type { Node } from "../src/views/elements";
import { ASK_KEY, askView, paneView, recapRows, recapView, tabBar, tabsView, type RecapRow } from "../src/views/pane";
import { health } from "../src/views/review";
import { fixture } from "./fake-host";

const reply = (name: string) => JSON.parse(fixture(`ask/${name}.json`)) as unknown;
const risk = (name: string) => JSON.parse(fixture(`change-risk/${name}.json`)) as ChangeRisk;
/** The recap's label and value rows by label; sub-heads left out. */
const byLabel = (rows: RecapRow[]) => Object.fromEntries(rows.filter((r): r is [string, string] => typeof r !== "string"));
const run = (actions: SessionAction[], from: SessionState = initialSession) => actions.reduce(reduce, from);

function texts(node: Node | null): string[] {
  if (node === null) return [];
  if (node.type === "Text") return node.children;
  if (node.type === "Button") return [`${node.props.hotkey}: ${node.props.label}`];
  if (node.type === "Markdown") return [node.props.text];
  if (node.type !== "Box") return [];
  return node.children.flatMap(texts);
}

describe("routing", () => {
  it("a why question reads the decision records; anything else asks get_answer", () => {
    expect(askRoute("why is QuerySet lazy?")).toEqual({ tool: "get_why", args: { query: "why is QuerySet lazy?" } });
    expect(askRoute("  Why not?")).toEqual({ tool: "get_why", args: { query: "  Why not?" } });
    expect(askRoute("whyever")).toEqual({ tool: "get_answer", args: { question: "whyever" } });
    expect(askRoute("how does filter() build SQL?")).toEqual({ tool: "get_answer", args: { question: "how does filter() build SQL?" } });
  });

  it("/lens arguments: a tab, or a question that opens the Ask field", () => {
    expect(lensCommand("")).toEqual({ tab: null, question: null });
    expect(lensCommand("recap")).toEqual({ tab: "recap", question: null });
    expect(lensCommand(" MAP ")).toEqual({ tab: "map", question: null });
    expect(lensCommand("flow")).toEqual({ tab: "flow", question: null });
    expect(lensCommand("ask")).toEqual({ tab: "ask", question: null });
    expect(lensCommand("ask why is QuerySet lazy?")).toEqual({ tab: "ask", question: "why is QuerySet lazy?" });
    expect(lensCommand("frobnicate")).toEqual({ tab: null, question: null });
    // A tab not on the bar is not a tab.
    expect(lensCommand("trail")).toEqual({ tab: null, question: null });
    expect(lensCommand("flow", ["map", "recap"])).toEqual({ tab: null, question: null });
    expect(lensCommand("ask how?", ["map", "recap"])).toEqual({ tab: "ask", question: "how?" });
  });

  it("only an answer that says it had no provider is known to have used no model", () => {
    expect(mayHaveUsedModel(askReply("get_answer", reply("django-answer-filter")))).toBe(false);
    expect(mayHaveUsedModel(askReply("get_answer", { answer: "synthesized", confidence: "high" }))).toBe(true);
    expect(mayHaveUsedModel(askReply("get_why", reply("requests-why-archaeology")))).toBe(false);
    expect(askReply("get_answer", null)).toEqual({ tool: "get_answer", reply: {} });
  });

  it("the Why draft names the decision", () => {
    expect(whyDraft("QuerySet evaluation stays lazy")).toBe("why QuerySet evaluation stays lazy");
  });
});

describe("Ask replies as Markdown", () => {
  it("get_answer: confidence, retrieval and the reason it is not a synthesis, verbatim, then the cited files", () => {
    const md = replyMarkdown("how does filter() build SQL?", askReply("get_answer", reply("django-answer-filter")));
    expect(md.split("\n\n")).toEqual([
      "> how does filter() build SQL?",
      "**Built from the index** · confidence low · retrieval weak",
      "Synthesis is unavailable (no-llm-provider). Source rationale in django/utils/tree.py: A class for storing a tree graph. Primarily used for filter constructs in the ORM.",
      "evidence: `django/utils/tree.py`, `django/db/models/sql/constants.py`",
    ]);
    expect(replyMarkdown("q", askReply("get_answer", { answer: "x" }))).toContain("no evidence cited");
    // No `degraded`: the reply may be the configured model's synthesis, and says so.
    expect(replyMarkdown("q", askReply("get_answer", { answer: "x", confidence: "high" }))).toContain(
      "**Written by this repo's configured model, from the index** · confidence high",
    );
    const named = replyMarkdown("where is QuerySet defined?", askReply("get_answer", reply("django-answer-queryset")));
    expect(named).toContain("**Built from the index** · confidence medium · retrieval high");
    expect(named).toContain("evidence: `django/db/models/query.py`");
  });

  it("get_why: the basis and reason, each commit with its evidence id", () => {
    const md = replyMarkdown("why does Session merge environment settings?", askReply("get_why", reply("requests-why-archaeology")));
    expect(md).toContain("**Built from the index** · basis archaeology");
    expect(md).toContain("No decision record covers this question.");
    expect(md).toContain("- `827bbe2a7e40` docs: correct error in 'merge_environment_settings' usage (2019-02-04) · `ev_8964f795d9001bd297ca`");
  });

  it("get_why on a path: five rows of each list, the rest counted against the server's own total", () => {
    const w = reply("django-why-query-path") as { git_archaeology: { git_log: unknown[] }; code_rationale_total: number };
    const md = replyMarkdown("why django/db/models/query.py", askReply("get_why", w));
    expect(md).toContain("**Built from the index** · basis rationale");
    expect(md).toContain(`- and ${w.git_archaeology.git_log.length - 5} more`);
    expect(md).toContain(`- and ${w.code_rationale_total - 5} more`);
    expect(md).toMatch(/- `django\/db\/models\/query\.py:\d+` .+ · `ev_[0-9a-f]+`/);
    expect(md.length).toBeLessThanOrEqual(ASK_CHARS);
  });

  it("get_why with no record says so, as the server put it", () => {
    const md = replyMarkdown("why rotate?", askReply("get_why", reply("django-why-no-record")));
    expect(md.split("\n\n")).toEqual([
      "> why rotate?",
      "**Built from the index**",
      "No recorded rationale: no decision record covers this question. The store holds none carrying its terms, and the closest ones would be noise.",
    ]);
  });

  it("a decision row carries its status, authority, confidence and id as sent", () => {
    // The row shape `_governing_decision_entry` serializes (tool_why/projection.py).
    const a: AskReply = {
      tool: "get_why",
      reply: { decisions: [{ id: "dec_1", title: "Querysets stay lazy", status: "active", authority: "accepted", confidence: 0.9, decision: "Defer\nthe query." }] },
    };
    expect(replyMarkdown("why lazy", a)).toContain("- **Querysets stay lazy** (active · accepted · confidence 0.9) · `dec_1`: Defer the query.");
  });

  it("a long reply is cut at the stated limit, and says so", () => {
    const md = replyMarkdown("q", askReply("get_answer", { answer: "x".repeat(9_000) }));
    expect(md.length).toBeLessThanOrEqual(ASK_CHARS);
    expect(md.endsWith("_reply cut at 4,000 characters_")).toBe(true);
  });
});

describe("tabs and the Ask tab", () => {
  it("draws the tab bar with the shown one at full strength; the Ask field is off the bar", () => {
    const tabs = tabsView("map");
    expect(texts(tabs)).toEqual(["1: Flow", "2: Map", "3: Recap"]);
    if (tabs.type !== "Box") throw new Error("expected a Box");
    expect(tabs.children.map((c) => (c.type === "Button" ? c.props.dimColor === true : null))).toEqual([true, false, true]);
    const asking = tabsView("ask");
    if (asking.type !== "Box") throw new Error("expected a Box");
    expect(asking.children.every((c) => c.type === "Button" && c.props.dimColor === true)).toBe(true);
    expect(texts(tabsView("map", tabBar(false)))).toEqual(["1: Map", "2: Recap"]);
    expect(tabBar(true)).toEqual(["flow", "map", "recap"]);
  });

  it("the field takes the keyboard when the pane does, pre-filled only by Why", () => {
    const idle = askView(initialSession, 80);
    if (idle.type !== "Box") throw new Error("expected a Box");
    expect(idle.children[0]).toMatchObject({ type: "Input", props: { key: ASK_KEY, autoFocus: true } });
    expect((idle.children[0]!.props as { value?: string }).value).toBeUndefined();
    const why = askView(run([{ type: "tab", tab: "ask", draft: "why X" }]), 80);
    if (why.type !== "Box") throw new Error("expected a Box");
    expect(why.children[0]).toMatchObject({ props: { value: "why X" } });
  });

  it("asking, failed and answered each say so; asking empties the field", () => {
    const asking = run([{ type: "tab", tab: "ask", draft: "why X" }, { type: "asked", question: "why X", tool: "get_why" }]);
    expect(asking.pane.draft).toBe("");
    expect(texts(askView(asking, 80)).slice(-1)).toEqual(["asking the decision records..."]);
    const failed = reduce(asking, { type: "askFailed", question: "why X", tool: "get_why", message: "timed out" });
    expect(texts(askView(failed, 80)).slice(-1)).toEqual(["Could not answer: timed out"]);
    const answered = reduce(asking, { type: "answered", question: "why X", answer: askReply("get_why", reply("django-why-no-record")) });
    expect(texts(askView(answered, 80)).at(-1)).toContain("**Built from the index**");
  });

  it("paneView puts the tabs over the body", () => {
    expect(texts(paneView("recap", { type: "Text", props: {}, children: ["body"] }))).toEqual(["1: Flow", "2: Map", "3: Recap", "body"]);
  });
});

/** A session that edited two files, saw a decision, reviewed, saved, and asked. */
function busySession(): SessionState {
  return run([
    { type: "discovered", mode: "full", freshness: null, repoRoot: "C:\\work\\requests" },
    { type: "touched", path: "src/requests/sessions.py" },
    { type: "touched", path: "src/requests/models.py" },
    { type: "touched", path: "src/requests/sessions.py" },
    { type: "notesFor", id: "toolu_1", notes: [{ kind: "decision", reviewed: true, title: "Sessions own their adapters" }] },
    { type: "notesFor", id: "toolu_2", notes: [{ kind: "decision", reviewed: true, title: "Sessions own their adapters" }, { kind: "fixes", count: 3, age: "2 weeks ago", symbol: null }] },
    { type: "notesFor", id: "toolu_3", notes: [{ kind: "decision", reviewed: false, title: "Retry on 503" }] },
    { type: "reviewed", risk: risk("overlap") },
    { type: "savings", delta: { tokens: 18_240, inferredTokens: 1_000, usd: 0.24 } },
    { type: "answered", question: "q", answer: askReply("get_answer", reply("django-answer-filter")) },
  ]);
}

describe("recap", () => {
  it("every figure equals its source in the session model", () => {
    const s = busySession();
    const reads = { count: 41, capped: false };
    const rows = byLabel(recapRows(s, reads));
    const r = s.lastReview!;
    const hd = r.health_delta!;
    expect(rows["Files"]).toBe(`${s.touched.length} edited of ${reads.count} files touched`);
    expect(s.touched).toEqual(["src/requests/sessions.py", "src/requests/models.py"]);
    expect(rows["Code health"]).toBe(health(r).words);
    expect(rows["Findings"]).toBe(`${hd.resolved} resolved · ${hd.findings_total} new finding${hd.findings_total === 1 ? "" : "s"}`);
    const tests = r.impacted_tests!;
    expect(rows["Tests to run"]).toBe(
      `${tests.total ?? tests.tests_to_run!.length} test file${(tests.total ?? 0) === 1 ? "" : "s"}, ${tests.basis === "measured" ? "measured" : "inferred"}`,
    );
    expect(rows["Saved"]).toBe(savingsLine(s.savings!, Number.POSITIVE_INFINITY));
    const decisions = surfacedDecisions(s);
    expect(decisions).toEqual([
      { title: "Sessions own their adapters", reviewed: true },
      { title: "Retry on 503", reviewed: false },
    ]);
    expect(rows["Decisions surfaced"]).toBe(`${decisions.length}: Sessions own their adapters; Retry on 503`);
    const branches = r.branch_overlap!.branches!.map((b) => b.branch);
    expect(rows["Branches overlapping"]).toContain(`(${branches.join(", ")})`);
    expect(rows["Branches overlapping"]!.startsWith("◦ ")).toBe(true);
    expect(texts(recapView(s, reads, 200)).at(-1)).toBe("Lens made no model calls. Every figure here is read from the local index.");
  });

  it("the last review outlives its turn; a new session starts empty", () => {
    const s = reduce(busySession(), { type: "turnStarted" });
    expect(s.review.outcome).toEqual({ phase: "none" });
    expect(s.lastReview).not.toBeNull();
    const empty = byLabel(recapRows(initialSession, { count: 0, capped: false }));
    expect(empty).toEqual({
      Files: "0 edited of 0 files touched",
      "Change review": "none this session",
      Saved: "needs the local server: repowise serve --no-ui",
      "Decisions surfaced": "none",
    });
  });

  it("states what is unknown rather than zero: capped reads, no tests, no overlap block, nothing saved yet", () => {
    const { branch_overlap: _unreported, ...noOverlapBlock } = risk("clear");
    const clear = run([{ type: "discovered", mode: "full", freshness: null }, { type: "reviewed", risk: { ...noOverlapBlock, impacted_tests: {} } }]);
    const rows = byLabel(recapRows(clear, { count: 200, capped: true }));
    expect(rows["Files"]).toBe("0 edited of 200+ files touched");
    expect(rows["Tests to run"]).toBe("none named");
    expect(rows["Branches overlapping"]).toBe("not reported");
    expect(rows["Saved"]).toBe("nothing yet since this session started");
    const none = reduce(clear, { type: "reviewed", risk: { ...risk("clear"), branch_overlap: { branches: [] } } });
    expect(byLabel(recapRows(none, { count: 0, capped: false }))["Branches overlapping"]).toBe("none found");
    const unavailable = reduce(clear, { type: "reviewed", risk: risk("unavailable") });
    expect(byLabel(recapRows(unavailable, { count: 0, capped: false }))["Findings"]).toBe("not compared");
  });

  it("the footer counts get_answer asks from their start; a reply that had no provider is uncounted", () => {
    const asked = run([{ type: "asked", question: "q", tool: "get_answer" }]);
    expect(asked.modelAsks).toBe(1);
    expect(texts(recapView(asked, { count: 0, capped: false }, 300)).at(-1)).toBe(
      "Lens made no model calls. 1 Ask reply from get_answer may have been written by this repo's configured model.",
    );
    // Failed or timed out: it stays counted; the provider may have seen the question.
    expect(reduce(asked, { type: "askFailed", question: "q", tool: "get_answer", message: "timeout" }).modelAsks).toBe(1);
    expect(reduce(asked, { type: "answered", question: "q", answer: askReply("get_answer", { answer: "a" }) }).modelAsks).toBe(1);
    expect(reduce(asked, { type: "answered", question: "q", answer: askReply("get_answer", reply("django-answer-filter")) }).modelAsks).toBe(0);
    expect(run([{ type: "asked", question: "why", tool: "get_why" }]).modelAsks).toBe(0);
  });

  it("draws a dim sub-head over the review rows and aligns values to one label column", () => {
    const shown = texts(recapView(busySession(), { count: 41, capped: false }, 200));
    expect(shown[2]).toBe("from the last change review");
    expect(shown[0]).toBe("Files                 ");
    expect(shown[3]).toBe("Code health           ");
  });

  it("an errored or empty review does not replace the last one", () => {
    const s = busySession();
    expect(reduce(s, { type: "reviewed", risk: { error: "boom" } }).lastReview).toBe(s.lastReview);
    expect(reduce(s, { type: "reviewed", risk: risk("nothing-to-score") }).lastReview).toBe(s.lastReview);
  });
});

describe("compaction brief", () => {
  it("nothing to brief: no text, no band row, even after a compaction", () => {
    const s = reduce(initialSession, { type: "compacted" });
    expect(briefText(s)).toBeNull();
    expect(briefBandRow(s, 100)).toBeNull();
  });

  it("files, decisions in play and the last review's open items, under the budget", () => {
    const s = reduce(busySession(), { type: "compacted" });
    const brief = briefText(s)!;
    const lines = brief.split("\n");
    expect(lines[0]).toBe("The context was compacted. This brief is built from the Repowise index and this session's edits:");
    expect(lines[1]).toBe("Files edited: src/requests/sessions.py; src/requests/models.py");
    expect(lines[2]).toBe(
      "Decisions in play: Sessions own their adapters (standing decision); Retry on 503 (found in the code, not yet reviewed)",
    );
    expect(lines[3]).toMatch(/^Open review items: /);
    expect(lines[3]).toContain("tests to run, inferred from the dependency graph, not measured:");
    expect(lines[3]).toContain("◦ ");
    expect(brief.length).toBeLessThanOrEqual(BRIEF_CHARS);
  });

  it("stays under the budget however much the session holds, and counts what it left out", () => {
    const actions: SessionAction[] = Array.from({ length: 500 }, (_, i) => ({ type: "touched" as const, path: `src/pkg${i}/module_with_a_long_name_${i}.py` }));
    actions.push(...Array.from({ length: 50 }, (_, i) => ({ type: "notesFor" as const, id: `t${i}`, notes: [{ kind: "decision" as const, reviewed: true, title: `decision ${i} ${"x".repeat(400)}` }] })));
    actions.push({ type: "reviewed", risk: risk("findings") });
    const brief = briefText(run(actions))!;
    expect(brief.length).toBeLessThanOrEqual(BRIEF_CHARS);
    expect(brief).toMatch(/Files edited: .+; and \d+ more/);
    expect(brief).toMatch(/Decisions in play: .+and \d+ more/);
    expect(brief).toContain("Open review items:");
  });

  it("listWithin keeps as many items as fit, then the count; never more than its room", () => {
    expect(listWithin("Files:", ["a", "b", "c"], 100)).toBe("Files: a; b; c");
    expect(listWithin("Files:", ["aaaa", "bbbb", "cccc", "dddd"], 23)).toBe("Files: aaaa; and 3 more");
    expect(listWithin("Files:", ["a".repeat(50)], 20)).toBe("Files: and 1 more");
    expect(listWithin("Files:", ["a"], 5).length).toBeLessThanOrEqual(5);
  });

  it("the band offers it beside a pending review row (hotkey 4), or alone (hotkey 1), until pressed or a new turn", () => {
    const s = reduce(busySession(), { type: "compacted" });
    const band = bandView(s, { columns: 100, hasSurvey: false });
    expect(texts(band)).toContain("2: Why");
    expect(texts(band).slice(-2)).toEqual(["context compacted", "4: Brief Claude"]);
    expect(JSON.stringify(band)).toContain(BRIEF_PRESS);
    const alone = run([{ type: "touched", path: "a.py" }, { type: "compacted" }]);
    expect(texts(bandView(alone, { columns: 100, hasSurvey: false }))).toEqual(["context compacted", "1: Brief Claude"]);
    expect(reduce(s, { type: "briefDone" }).compacted).toBe(false);
    expect(reduce(s, { type: "turnStarted" }).compacted).toBe(false);
    expect(reduce(s, { type: "compacted" })).toBe(s);
    expect(reduce(initialSession, { type: "briefDone" })).toBe(initialSession);
  });

  it("joins an item written over several lines", () => {
    expect(listWithin("Decisions:", ["keep\n  it\tbounded"], 100)).toBe("Decisions: keep it bounded");
  });
});

describe("copy", () => {
  it("no em or en dash in anything the pane or the brief writes", async () => {
    const copy = await import("../src/views/copy");
    const all = [
      ...Object.values(copy.PANE_COPY).flatMap((v) => (typeof v === "string" ? [v] : Object.values(v))),
      ...Object.values(copy.RECAP_COPY),
      ...Object.values(copy.BRIEF_COPY),
      copy.recapFooter(2),
      copy.briefDecision("t", false),
    ];
    for (const line of all) expect(line).not.toMatch(/[–—]/);
  });
});
