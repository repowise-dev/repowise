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
import { ASK_KEY, askView, paneView, recapRows, recapView, tabsView } from "../src/views/pane";
import { health } from "../src/views/review";
import { fixture } from "./fake-host";

const reply = (name: string) => JSON.parse(fixture(`ask/${name}.json`)) as unknown;
const risk = (name: string) => JSON.parse(fixture(`change-risk/${name}.json`)) as ChangeRisk;
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

  it("/lens arguments: a tab, or ask with its question", () => {
    expect(lensCommand("")).toEqual({ tab: null, question: null });
    expect(lensCommand("recap")).toEqual({ tab: "recap", question: null });
    expect(lensCommand(" MAP ")).toEqual({ tab: "map", question: null });
    expect(lensCommand("ask")).toEqual({ tab: "ask", question: null });
    expect(lensCommand("ask why is QuerySet lazy?")).toEqual({ tab: "ask", question: "why is QuerySet lazy?" });
    expect(lensCommand("frobnicate")).toEqual({ tab: null, question: null });
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
      "**get_answer** · confidence: low · retrieval: weak · not synthesized: no-llm-provider",
      "Synthesis is unavailable (no-llm-provider). Source rationale in django/utils/tree.py: A class for storing a tree graph. Primarily used for filter constructs in the ORM.",
      "cited: `django/utils/tree.py`, `django/db/models/sql/constants.py`",
    ]);
    expect(replyMarkdown("q", askReply("get_answer", { answer: "x" }))).toContain("no evidence cited");
    const named = replyMarkdown("where is QuerySet defined?", askReply("get_answer", reply("django-answer-queryset")));
    expect(named).toContain("**get_answer** · confidence: medium · retrieval: high · not synthesized: no-llm-provider");
    expect(named).toContain("cited: `django/db/models/query.py`");
  });

  it("get_why: the basis and reason, each commit with its evidence id", () => {
    const md = replyMarkdown("why does Session merge environment settings?", askReply("get_why", reply("requests-why-archaeology")));
    expect(md).toContain("**get_why** · basis: archaeology");
    expect(md).toContain("No decision record covers this question.");
    expect(md).toContain("- `827bbe2a7e40` docs: correct error in 'merge_environment_settings' usage (2019-02-04) · `ev_8964f795d9001bd297ca`");
  });

  it("get_why on a path: five rows of each list, the rest counted against the server's own total", () => {
    const w = reply("django-why-query-path") as { git_archaeology: { git_log: unknown[] }; code_rationale_total: number };
    const md = replyMarkdown("why django/db/models/query.py", askReply("get_why", w));
    expect(md).toContain("**get_why** · basis: rationale");
    expect(md).toContain(`- and ${w.git_archaeology.git_log.length - 5} more`);
    expect(md).toContain(`- and ${w.code_rationale_total - 5} more`);
    expect(md).toMatch(/- `django\/db\/models\/query\.py:\d+` .+ · `ev_[0-9a-f]+`/);
    expect(md.length).toBeLessThanOrEqual(ASK_CHARS);
  });

  it("get_why with no record says so, as the server put it", () => {
    const md = replyMarkdown("why rotate?", askReply("get_why", reply("django-why-no-record")));
    expect(md.split("\n\n")).toEqual([
      "> why rotate?",
      "**get_why**",
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
  it("draws three tabs with the shown one at full strength", () => {
    const tabs = tabsView("ask");
    expect(texts(tabs)).toEqual(["1: Map", "2: Ask", "3: Recap"]);
    if (tabs.type !== "Box") throw new Error("expected a Box");
    expect(tabs.children.map((c) => (c.type === "Button" ? c.props.dimColor === true : null))).toEqual([true, false, true]);
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
    expect(texts(askView(asking, 80)).slice(-1)).toEqual(["asking get_why..."]);
    const failed = reduce(asking, { type: "askFailed", question: "why X", tool: "get_why", message: "timed out" });
    expect(texts(askView(failed, 80)).slice(-1)).toEqual(["get_why could not answer: timed out"]);
    const answered = reduce(asking, { type: "answered", question: "why X", answer: askReply("get_why", reply("django-why-no-record")) });
    expect(texts(askView(answered, 80)).at(-1)).toContain("**get_why**");
  });

  it("paneView puts the tabs over the body", () => {
    expect(texts(paneView("recap", { type: "Text", props: {}, children: ["body"] }))).toEqual(["1: Map", "2: Ask", "3: Recap", "body"]);
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
    const rows = Object.fromEntries(recapRows(s, reads));
    const r = s.lastReview!;
    const hd = r.health_delta!;
    expect(rows["Files touched"]).toBe(`${s.touched.length} edited · ${reads.count} read or edited`);
    expect(s.touched).toEqual(["src/requests/sessions.py", "src/requests/models.py"]);
    expect(rows["Code health (last review)"]).toBe(health(r).words);
    expect(rows["Findings (last review)"]).toBe(`${hd.resolved} resolved · ${hd.findings_total} new`);
    const tests = r.impacted_tests!;
    expect(rows["Tests queued (last review)"]).toBe(
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
    expect(rows["Branches overlapping (last review)"]).toContain(`(${branches.join(", ")})`);
    expect(rows["Branches overlapping (last review)"]!.startsWith("◦ ")).toBe(true);
    expect(texts(recapView(s, reads, 200)).at(-1)).toBe("0 model calls · nothing uploaded");
  });

  it("the last review outlives its turn; a new session starts empty", () => {
    const s = reduce(busySession(), { type: "turnStarted" });
    expect(s.review.outcome).toEqual({ phase: "none" });
    expect(s.lastReview).not.toBeNull();
    const empty = Object.fromEntries(recapRows(initialSession, { count: 0, capped: false }));
    expect(empty).toEqual({
      "Files touched": "0 edited · 0 read or edited",
      "Code health (last review)": "no change review this session",
      Saved: "needs the local server: repowise serve --no-ui",
      "Decisions surfaced": "none",
    });
  });

  it("states what is unknown rather than zero: capped reads, no tests, no overlap block, nothing saved yet", () => {
    const { branch_overlap: _unreported, ...noOverlapBlock } = risk("clear");
    const clear = run([{ type: "discovered", mode: "full", freshness: null }, { type: "reviewed", risk: { ...noOverlapBlock, impacted_tests: {} } }]);
    const rows = Object.fromEntries(recapRows(clear, { count: 200, capped: true }));
    expect(rows["Files touched"]).toBe("0 edited · 200+ read or edited");
    expect(rows["Tests queued (last review)"]).toBe("none named");
    expect(rows["Branches overlapping (last review)"]).toBe("not reported");
    expect(rows["Saved"]).toBe("nothing yet since this session started");
    const none = reduce(clear, { type: "reviewed", risk: { ...risk("clear"), branch_overlap: { branches: [] } } });
    expect(Object.fromEntries(recapRows(none, { count: 0, capped: false }))["Branches overlapping (last review)"]).toBe("none found");
    const unavailable = reduce(clear, { type: "reviewed", risk: risk("unavailable") });
    expect(Object.fromEntries(recapRows(unavailable, { count: 0, capped: false }))["Findings (last review)"]).toBe("not compared");
  });

  it("the footer names get_answer replies that may have used the repo's model", () => {
    const s = reduce(initialSession, { type: "answered", question: "q", answer: askReply("get_answer", { answer: "a" }) });
    expect(s.modelAsks).toBe(1);
    expect(texts(recapView(s, { count: 0, capped: false }, 300)).at(-1)).toBe(
      "Lens: 0 model calls · nothing uploaded · 1 Ask reply from get_answer may have used this repo's configured model",
    );
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
    expect(lines[0]).toBe("The context was compacted. Lens brief of this session, from Repowise index lookups (no model):");
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

  it("the band offers it in place of the review's buttons until pressed or a new turn", () => {
    const s = reduce(busySession(), { type: "compacted" });
    const band = bandView(s, { columns: 100, hasSurvey: false });
    expect(texts(band).slice(-2)).toEqual(["context compacted", "1: Brief Claude"]);
    expect(JSON.stringify(band)).toContain(BRIEF_PRESS);
    expect(reduce(s, { type: "briefDone" }).compacted).toBe(false);
    expect(reduce(s, { type: "turnStarted" }).compacted).toBe(false);
    expect(reduce(s, { type: "compacted" })).toBe(s);
    expect(reduce(initialSession, { type: "briefDone" })).toBe(initialSession);
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
