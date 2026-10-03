// The Flow tab as element trees: the one-line owl and its states, the theme
// and its color rules, the copy's units, and each section of the dashboard at
// wide and narrow widths.
import { describe, expect, it } from "vitest";
import { BRAND, DARK, LIGHT } from "@repowise-dev/ui/brand";
import { activityOf, initialFlow, reduceFlow, toolEnded, type FlowAction, type FlowState } from "../src/model/flow";
import type { ChangeRisk } from "../src/model/review";
import { summarizeReply } from "../src/model/replies";
import type { FileContext } from "../src/model/session";
import { blastFacts, type BlastResponse } from "../src/model/turnFacts";
import {
  FLOW_COPY,
  answerLine,
  behindParts,
  clockText,
  cochangeLine,
  durationText,
  freshnessText,
  importersLine,
  insideFact,
  introducedLine,
  knowsParts,
  sizeText,
  statusLine,
  testsReachLine,
  turnFooter,
} from "../src/views/copy";
import type { Node } from "../src/views/elements";
import { ACTIVE_MARK, FLOW_KEY, FLOW_NEXT, FLOW_PREV, HAPPY_MS, flowPresses, flowView, rowKey, type FlowViewInput } from "../src/views/flow";
import { BLINK_MS, EYES, owl, owlEyes } from "../src/views/owl";
import { THISTLE, flowTheme, hills, loch } from "../src/views/theme";
import { fixture } from "./fake-host";

const ROOT = "C:\\work\\django";
const QUERY = "django/db/models/query.py";
const DARK_THEME = flowTheme(false);
const call = (tool: string, id: string, args: Record<string, unknown> = {}) => ({ tool, tool_use_id: id, ...args });
const start = (tool: string, id: string, at: number, args: Record<string, unknown> = {}): FlowAction => ({
  type: "toolStarted",
  activity: activityOf(call(tool, id, args), at, ROOT)!,
});
const end = (tool: string, id: string, at: number, outcome: unknown): FlowAction => toolEnded(call(tool, id), outcome, at, ROOT)!;
const run = (actions: FlowAction[], from: FlowState = initialFlow) => actions.reduce(reduceFlow, from);
const abs = (rel: string) => `${ROOT}\\${rel.replace(/\//g, "\\")}`;

/** One drawn line per child of the tab's column; rows run their runs together. */
function line(n: Node): string {
  if (n.type === "Text") return n.children.join("");
  // A row's Button has no hotkey and draws its label alone; the bar's draw `j: label`.
  if (n.type === "Button") return n.props.hotkey === undefined ? n.props.label : `[${n.props.hotkey}: ${n.props.label}]`;
  if (n.type === "Markdown") return n.props.text;
  if (n.type !== "Box") return "";
  return n.children.map(line).join(n.props.flexDirection === "row" ? " ".repeat(n.props.columnGap ?? 0) : "\n");
}
const lines = (n: Node): string[] => (n.type === "Box" ? n.children.map(line) : [line(n)]).flatMap((l) => l.split("\n"));

function all(n: Node): Node[] {
  return n.type === "Box" ? [n, ...n.children.flatMap(all)] : [n];
}
const texts = (n: Node) => all(n).filter((x): x is Extract<Node, { type: "Text" }> => x.type === "Text");
const colored = (n: Node, color: string) => texts(n).filter((x) => x.props.color === color).map(line);
const strong = (n: Node) => texts(n).filter((x) => x.props.dimColor !== true).map(line);

const CARD: FileContext = { callerFiles: 131, contributors: 172, hotspot: true, recentOwner: { name: "author_two", share: 0.2857 } };
const view = (s: FlowState, o: Partial<FlowViewInput> = {}) =>
  flowView(s, { columns: 120, rows: 50, now: 10_000, still: false, mode: "full", contexts: { [QUERY]: CARD }, review: null, ...o });
const BLAST = JSON.parse(fixture("flow/blast_query.json")) as BlastResponse;

/** A Django turn: get_context names query.py, Claude reads it and a search hit, edits query.py, runs the queries tests, asks get_risk after the edit. */
function working(): FlowState {
  return run([
    { type: "turnStarted", turnId: "t1", prompt: "<system-reminder>x</system-reminder>add a comment above bulk_create", at: 1_000 },
    start("mcp__repowise__get_context", "c1", 3_000, { targets: [QUERY] }),
    end("mcp__repowise__get_context", "c1", 3_420, { text: fixture("flow/get_context.json") }),
    start("Read", "r1", 5_000, { file_path: abs(QUERY) }),
    end("Read", "r1", 5_100, { text: "x".repeat(12_000) }),
    start("Grep", "g1", 6_000, { pattern: "bulk_create" }),
    end("Grep", "g1", 6_100, { result: { filenames: ["django\\db\\models\\base.py"] }, text: "y".repeat(300) }),
    start("Read", "r2", 6_200, { file_path: abs("django/db/models/base.py") }),
    end("Read", "r2", 6_300, { text: "z".repeat(8_000) }),
    start("Edit", "e1", 42_000, { file_path: abs(QUERY), old_string: "a", new_string: "# x\na" }),
    end("Edit", "e1", 42_100, { text: "ok" }),
    { type: "blastLanded", path: QUERY, facts: blastFacts(QUERY, BLAST) },
    start("Bash", "b1", 44_000, { command: "python tests/runtests.py queries" }),
    end("Bash", "b1", 50_000, { text: "w".repeat(2_048) }),
    start("mcp__repowise__get_risk", "c2", 50_500, { targets: [QUERY] }),
  ]);
}
const finished = (how: "answer" | "stopped" | "failed" = "answer") =>
  run([end("mcp__repowise__get_risk", "c2", 51_100, { text: fixture("flow/get_risk.json") }), { type: "turnEnded", at: 52_000, durationMs: 51_000, end: how }], working());

const INTRODUCED = {
  health_delta: { status: "available", introduced: 1, worsened: 0, resolved: 0, top_findings: [{ change: "introduced", biomarker: "complex_method", path: QUERY }] },
} as unknown as ChangeRisk;

describe("the owl", () => {
  it("one line, five cells: eyes say the state, strokes keep the terminal's color, open eyes amber", () => {
    expect(owl("watching", EYES.open, "#f59520").map(line).join("")).toBe("{◉,◉}");
    expect(colored({ type: "Box", props: {}, children: owl("asking", EYES.side, "#f59520") }, "#f59520")).toEqual(["◐", "◐"]);
    const asleep = owl("asleep", EYES.shut, "#f59520");
    expect(asleep.map(line)).toEqual(["{─,─}"]);
    expect(asleep[0]).toMatchObject({ props: { dimColor: true } });
    expect(texts({ type: "Box", props: {}, children: owl("stopped", EYES.still, "#f59520") }).every((t) => t.props.color === undefined)).toBe(true);
  });

  it("asleep, a slow blink while working (none with reduced motion), aside while Repowise answers, happy, a neutral dot after a stop", () => {
    expect(owlEyes("asleep", 0, false)).toBe("─");
    expect(owlEyes("asking", 0, false)).toBe("◐");
    expect(owlEyes("happy", 0, false)).toBe("^");
    expect(owlEyes("stopped", 0, false)).toBe("•");
    expect([0, 8, 9, 10].map((f) => owlEyes("watching", f * BLINK_MS, false))).toEqual(["◉", "◉", "─", "◉"]);
    expect(owlEyes("watching", 9 * BLINK_MS, true)).toBe("◉");
  });
});

describe("the theme and its color rules", () => {
  it("two accents from the brand ramps: dark by default, the light ramp on a light terminal", () => {
    expect(flowTheme(false)).toEqual({ plum: DARK.accentSecondary, amber: BRAND.accent });
    expect(flowTheme(true)).toEqual({ plum: LIGHT.accentSecondary, amber: BRAND.accentTextLight });
  });

  it("the same hills at any width, and their reflection", () => {
    expect(hills(12)).toBe("▁▂▃▅▃▂▁▁▂▃▂▁");
    expect(hills(45)).toHaveLength(45);
    expect(hills(0)).toBe("");
    expect(loch(6)).toBe(" ▔▔▀▔▔");
    expect(THISTLE).toBe("⚘");
  });

  it("no Flow text sets a background; readable text keeps the terminal's color; only marks take an accent", () => {
    for (const light of [false, true]) {
      const theme = flowTheme(light);
      const states = [initialFlow, working(), finished(), reduceFlow(finished(), { type: "toggle", id: "c1" })];
      for (const s of states) {
        const tree = view(s, { light, review: INTRODUCED, mode: "no-index" });
        for (const t of texts(tree)) expect(t.props).not.toHaveProperty("backgroundColor");
        const marks = texts(tree).filter((t) => t.props.color !== undefined);
        for (const m of marks) expect([theme.plum, theme.amber]).toContain(m.props.color);
        // Marks are short: glyphs, a tool name, the hills; never a sentence.
        for (const m of marks) expect(line(m).trim().length <= 16 || /^[▁▂▃▄▅█]+$/.test(line(m))).toBe(true);
      }
    }
  });
});

describe("Flow copy", () => {
  it("every figure has its unit", () => {
    expect([clockText(42_000), clockText(-5), clockText(725_000)]).toEqual(["0:42", "0:00", "12:05"]);
    expect([durationText(420), durationText(2_200), durationText(41_000), durationText(192_000)]).toEqual(["420 ms", "2.2 s", "41 s", "3 min 12 s"]);
    expect([sizeText(820), sizeText(2_150), sizeText(3 * 1024 * 1024)]).toEqual(["820 B", "2.1 KB", "3.0 MB"]);
  });

  it("the status and the answer line", () => {
    expect(statusLine(null, 42_000, 0)).toBe("Working · 0:42");
    expect(statusLine("answer", 41_000, 1)).toBe("Done · 41 s · edited 1 file");
    expect(statusLine("stopped", 2_000, 2)).toBe("Stopped · 2.0 s · edited 2 files");
    expect(statusLine("failed", 900, 0)).toBe("Did not finish · 900 ms");
    expect(answerLine({ repowise: 3, opened: 4, named: 2, editNamed: true })).toBe(
      "Repowise 3 calls · Claude opened 4 files, 2 named by Repowise first · edit landed in a file Repowise named",
    );
    expect(answerLine({ repowise: 1, opened: 1, named: 0, editNamed: false })).toBe("Repowise 1 call · Claude opened 1 file");
    expect(answerLine({ repowise: 0, opened: 0, named: 0, editNamed: false })).toBe("No Repowise calls");
  });

  it("before you accept, said with the server's units and bases", () => {
    expect(importersLine(12, 1)).toBe("12 direct importers; Claude opened 1");
    expect(importersLine(1, 0)).toBe("1 direct importer; Claude opened none");
    expect(cochangeLine([{ path: "tests/queries/tests.py", score: 0.8024 }])).toBe("not opened, usually changes with it (co-change score): tests/queries/tests.py 0.80");
    expect(testsReachLine(17, "inferred", 0, "query.py")).toBe("17 test files reach query.py (inferred); none run");
    expect(testsReachLine(1, "measured", 1, "a.py")).toBe("1 test file reach a.py (measured); 1 run");
    expect(introducedLine(1, { biomarker: "complex_method", path: QUERY })).toBe("this turn introduced 1 finding: complex method in query.py");
    expect(introducedLine(2, null)).toBe("this turn introduced 2 findings");
  });

  it("what the index knows of a file, and how fresh a reply's index was", () => {
    expect(knowsParts(CARD)).toEqual(["hotspot", "131 files use it", "recent owner author_two 29 %"]);
    expect(knowsParts({ callerFiles: 1, hotspot: false, recentOwner: null })).toEqual(["1 file uses it"]);
    expect(knowsParts({ callerFiles: 0, hotspot: null, recentOwner: null })).toEqual([]);
    expect(freshnessText({ indexBehind: false, ageDays: 0 })).toBe("index current");
    expect(freshnessText({ indexBehind: true, ageDays: 3 })).toBe("index behind HEAD");
    expect(freshnessText({ indexBehind: null, ageDays: 3 })).toBe("index 3 days old");
    expect(freshnessText({ indexBehind: null, ageDays: null })).toBeNull();
  });

  it("earlier turns in one line; contains facts with their unit; how it was answered says only what the reply said", () => {
    const t = { repowise: 2, reads: 3, edits: 0 };
    expect(turnFooter(2, 41_000, t)).toBe("Turn 2 · 41 s · 2 Repowise calls · 3 files read · 0 edited");
    expect(turnFooter(2, null, t, "stopped")).toBe("Turn 2 · stopped · 2 Repowise calls · 3 files read · 0 edited");
    expect(turnFooter(2, 1_000, t, null)).toBe("Turn 2 · 1.0 s · 2 Repowise calls · 3 files read · 0 edited");
    expect(insideFact({ what: "symbols", n: 40, of: 180 })).toBe("40 of 180 symbols");
    expect(insideFact({ what: "dead_files", n: 3 })).toBe("3 dead files");
    const risk = summarizeReply("get_risk", fixture("flow/get_risk.json"));
    expect(behindParts(risk.behind, risk.bytes)).toEqual([
      "indexed at e78991410b78",
      "index 0 days old",
      "partial: the server capped it",
      "reply 3.6 KB (cap 24,000 characters)",
      "1,849 tokens left out, restorable",
      "semantic search off",
    ]);
    expect(behindParts(risk.behind)).toContain("reply 3,715 characters (cap 24,000 characters)");
    const symbol = summarizeReply("get_symbol", fixture("flow/get_symbol.json")).behind;
    expect(behindParts(symbol)).toContain("verified against the code");
    expect(behindParts({ ...symbol, verified: false })).toContain("not verified");
    const answer = summarizeReply("get_answer", fixture("flow/get_answer.json")).behind;
    expect(behindParts(answer)).toEqual(expect.arrayContaining(["confidence medium", "grounding symbol body", "retrieval high", "degraded: no-llm-provider"]));
  });
});

describe("the Flow tab", () => {
  it("empty: the owl asleep and what the tab will show, wrapped to 70 columns", () => {
    const tree = view(initialFlow);
    expect(tree.type === "Box" && tree.props.key).toBe(FLOW_KEY);
    const shown = lines(tree);
    expect(shown[0]).toBe(hills(120));
    expect(shown[1]).toBe("{─,─} Lens is listening. Send a prompt and this tab shows what only Lens");
    expect(shown.slice(2, 4).every((l) => l.length <= 76)).toBe(true);
    expect(lines(view(initialFlow, { mode: "no-cli" }))).toContain(FLOW_COPY.notConnected);
    expect(flowPresses(initialFlow)).toEqual([[FLOW_NEXT, null], [FLOW_PREV, null]]);
  });

  it("while Claude works: the owl and status, the answer at full strength, the user's own words dim, three lines in all", () => {
    const tree = view(working(), { now: 1_000 + 52 * 1_000 });
    const shown = lines(tree);
    expect(shown[1]).toBe("{◐,◐} Working · 0:52 · edited 1 file");
    expect(shown[2]).toBe("Repowise 2 calls · Claude opened 2 files, 1 named by Repowise first · edit landed in a file Repowise named");
    expect(strong(tree)).toContain(shown[2]);
    expect(shown[3]).toBe('"add a comment above bulk_create"');
    expect(strong(tree)).not.toContain(shown[3]);
    expect(shown[4]).toBe(FLOW_COPY.beforeAccept);
  });

  it("before you accept: per edited file what it reaches that Claude did not look at, as of the last index; the turn's health", () => {
    const shown = lines(view(finished(), { review: INTRODUCED }));
    const at = shown.indexOf(FLOW_COPY.beforeAccept);
    expect(shown[at + 1]).toMatch(/^ {2}django\/db\/models\/query\.py {2}as of the last index$/);
    // base.py, which Claude read, is one of the importers.
    expect(shown[at + 2]).toBe("    12 direct importers; Claude opened 1");
    expect(shown[at + 3]).toMatch(/^ {4}not opened, usually changes with it \(co-change score\): tests\/queries\/tests\.py 0\.80, /);
    expect(shown[at + 4]).toMatch(/^ {4}17 test files reach query\.py \(inferred\); \d+ run$/);
    expect(shown[at + 5]).toBe("  this turn introduced 1 finding: complex method in query.py");
    const partly = { health_delta: { status: "partial", introduced: 0, top_findings: [] } } as unknown as ChangeRisk;
    expect(lines(view(finished(), { review: partly }))).toContain(`  health compared in part only; no new findings in the part compared`);
    const clean = { health_delta: { status: "available", introduced: 0, top_findings: [] } } as unknown as ChangeRisk;
    expect(lines(view(finished(), { review: clean })).some((l) => l.includes("introduced"))).toBe(false);
    expect(lines(view(finished(), { review: {} as ChangeRisk })).some((l) => l.includes("introduced"))).toBe(false);
  });

  it("nothing to flag: no BEFORE YOU ACCEPT section at all", () => {
    const reads = run([{ type: "turnStarted", turnId: "t", prompt: "", at: 0 }, start("Read", "r", 1, { file_path: abs(QUERY) }), end("Read", "r", 2, { text: "x" })]);
    expect(lines(view(reads))).not.toContain(FLOW_COPY.beforeAccept);
  });

  it("the working set: edited first, then reads, how Claude came to each, and what the index knows", () => {
    const tree = view(finished());
    const shown = lines(tree);
    const at = shown.indexOf(`${FLOW_COPY.workingSet} · ${FLOW_COPY.asOfIndex}`);
    expect(shown[at + 1]).toBe("  django/db/models/query.py  edited  get_context  hotspot · 131 files use it · recent owner author_two 29 %");
    expect(shown[at + 2]).toBe("  django/db/models/base.py   read    search       ");
    expect(colored(tree, DARK_THEME.plum)).toContain("get_context  ");
    const narrow = lines(view(finished(), { columns: 70 }));
    expect(narrow.find((l) => l.startsWith("  django/db/models/query.py  edited"))).toBe("  django/db/models/query.py  edited  get_context  ");
  });

  it("the working set caps its rows and says how many more", () => {
    let s = run([{ type: "turnStarted", turnId: "t", prompt: "", at: 0 }]);
    for (let i = 0; i < 9; i++) s = run([start("Read", `r${i}`, i, { file_path: abs(`f${i}.py`) }), end("Read", `r${i}`, i, { text: "x" })], s);
    const shown = lines(view(s));
    expect(shown.filter((l) => /^ {2}f\d\.py/.test(l))).toHaveLength(6);
    expect(shown).toContain("  +3 more");
    expect(shown.find((l) => l.startsWith("  f0.py"))).toBe("  f0.py  read    direct       ");
  });

  it("context: one bar of measured bytes by kind, shades not colors, a legend with sizes, and the first edit", () => {
    const tree = view(finished());
    const shown = lines(tree);
    const at = shown.indexOf(FLOW_COPY.context);
    expect(shown[at + 1]).toMatch(/^ {2}█+▓+▒+░+·$/);
    expect(shown[at + 2]).toMatch(/^ {2}█ Repowise \d+\.\d KB {2}▓ file reads 19\.5 KB {2}▒ search 300 B {2}░ shell 2\.0 KB {2}· other 2 B {2}first edit after 41 s$/);
    expect(colored(tree, DARK_THEME.plum).some((t) => /^█+$/.test(t))).toBe(true);
    const nothing = run([{ type: "turnStarted", turnId: "t", prompt: "", at: 0 }]);
    expect(lines(view(nothing))).not.toContain(FLOW_COPY.context);
  });

  it("Repowise calls: one line each with time, tool, target, latency and what the reply was built from; the call in flight marked", () => {
    const tree = view(working());
    const shown = lines(tree);
    const at = shown.indexOf(FLOW_COPY.calls);
    expect(shown[at + 1]).toMatch(/^ {2}0:02 {2}get_context {2}django\/db\/models\/query\.py +420 ms · 1 target · 1 documentation page · index current$/);
    expect(shown[at + 1]).toHaveLength(120);
    expect(shown[at + 2]).toMatch(/^▸ 0:49 {2}get_risk {2}django\/db\/models\/query\.py +asking\.\.\.$/);
    expect(colored(tree, DARK_THEME.amber)).toContain(`${ACTIVE_MARK} `);
    expect(all(tree).find((n) => n.type === "Button" && n.props.label === "get_context")).toMatchObject({ props: { key: rowKey("c1") } });
    expect(shown.at(-1)).toBe("[j: Next call]  [k: Previous call]");
  });

  it("a call about a file Claude had already edited says, dim and plain, that its answer came from the index before the edit", () => {
    const tree = view(finished());
    const row = lines(tree).find((l) => l.includes("get_risk"))!;
    expect(row).toContain("from the index before this edit");
    expect(lines(tree).find((l) => l.includes("get_context  django"))).not.toContain("before this edit");
    const note = texts(tree).find((t) => line(t).includes("from the index before this edit"))!;
    expect(note.props).toEqual({ dimColor: true });
    const open = lines(view(reduceFlow(finished(), { type: "toggle", id: "c2" }), { columns: 90 }));
    expect(open.find((l) => l.includes("how it was answered"))).toMatch(/how it was answered from the index before this edit · indexed at e78991410b78/);
  });

  it("an open call: how it was answered, its first characters, and what Claude then opened", () => {
    const shown = lines(view(reduceFlow(finished(), { type: "toggle", id: "c1" })));
    const at = shown.findIndex((l) => l.includes("how it was answered"));
    // Its parts wrap at the pane's width rather than being cut.
    expect(shown.slice(at, at + 2)).toEqual([
      "        how it was answered indexed at e78991410b78 · index 0 days old · reply 7.4 KB (cap 24,000 characters)",
      "                            semantic search off",
    ]);
    expect(shown[at + 2]).toMatch(/^ {8}reply begins {8}\{"result":\{"targets":/);
    expect(shown[at + 3]).toBe(`        Claude then opened  ${QUERY}`);
    const lone = run([start("mcp__repowise__get_why", "w", 1), end("mcp__repowise__get_why", "w", 2, { text: "{}" }), { type: "toggle", id: "w" }]);
    expect(lines(view(lone)).find((l) => l.includes("Claude then opened"))).toContain(FLOW_COPY.nothingOpened);
    const waiting = run([{ type: "turnStarted", turnId: "t", prompt: "", at: 0 }, start("mcp__repowise__get_why", "x", 1), { type: "toggle", id: "x" }]);
    expect(lines(view(waiting)).some((l) => l.includes("how it was answered"))).toBe(false);
  });

  it("done: the owl happy for a moment, then asleep; the status stays; a thistle and the loch", () => {
    const happy = lines(view(finished(), { now: 52_000 + HAPPY_MS - 1 }));
    expect(happy[1]).toBe("{^,^} Done · 51 s · edited 1 file  ⚘");
    const later = lines(view(finished(), { now: 52_000 + 60 * 60_000 }));
    expect(later[1]).toBe("{─,─} Done · 51 s · edited 1 file  ⚘");
    expect(later).toContain(loch(120));
  });

  it("stopped or not finished: plain words, a neutral owl, no thistle, no loch", () => {
    for (const [how, word] of [["stopped", "Stopped"], ["failed", "Did not finish"]] as const) {
      const shown = lines(view(finished(how), { now: 52_001 }));
      expect(shown[1]).toBe(`{•,•} ${word} · 51 s · edited 1 file`);
      expect(shown.join("\n")).not.toContain(THISTLE);
      expect(shown).not.toContain(loch(120));
    }
  });

  it("subagent calls and failed calls say so", () => {
    const s = run([
      { type: "turnStarted", turnId: "t", prompt: "", at: 0 },
      start("mcp__repowise__get_why", "w", 1_000, { query: "q", agentId: "sub-1" }),
      end("mcp__repowise__get_why", "w", 2_000, null),
    ]);
    expect(lines(view(s)).find((l) => l.includes("get_why"))).toMatch(/get_why {2}q {2}subagent +error$/);
  });

  it("too many calls: the newest stay, or the open call's, with the calls above and below counted; earlier turns one line each", () => {
    let s = initialFlow;
    for (let t = 0; t < 8; t++) {
      s = run([{ type: "turnStarted", turnId: `t${t}`, prompt: `turn ${t}`, at: t * 100_000 }], s);
      for (let i = 0; i < 12; i++) s = reduceFlow(s, start("mcp__repowise__get_why", `w${t}-${i}`, t * 100_000 + i * 1_000, { query: `q${i}` }));
      s = reduceFlow(s, { type: "turnEnded", at: t * 100_000 + 50_000, durationMs: 50_000, end: t === 6 ? "stopped" : "answer" });
    }
    const shown = lines(view(s, { rows: 22, now: 10_000_000 }));
    expect(shown.find((l) => l.includes("hidden"))).toMatch(/^\d+ earlier calls hidden · j \/ k step through calls$/);
    expect(shown.some((l) => l.includes("q11"))).toBe(true);
    expect(shown).toContain("  Turn 7 · stopped · 50 s · 12 Repowise calls · 0 files read · 0 edited");
    expect(shown).toContain("  Earlier: 2 turns · 24 Repowise calls · 0 files read · 0 edited");
    const around = lines(view(reduceFlow(s, { type: "toggle", id: "w7-0" }), { rows: 22, now: 10_000_000 }));
    expect(around.some((l) => l.includes("q0"))).toBe(true);
    expect(around.some((l) => l.includes("q11"))).toBe(false);
    expect(around.find((l) => l.includes("later call"))).toMatch(/^\d+ later calls below · j \/ k step through calls$/);
  });

  it("says how many steps of a long turn were not kept", () => {
    let s = run([{ type: "turnStarted", turnId: "t", prompt: "", at: 0 }]);
    for (let i = 0; i < 65; i++) s = reduceFlow(s, start("mcp__repowise__get_why", `b${i}`, i, { query: `c${i}` }));
    expect(lines(view(s, { rows: 200 }))).toContain("  5 earlier steps of this turn not kept");
  });
});
