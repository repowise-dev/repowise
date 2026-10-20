import { describe, expect, it } from "vitest";
import { EDIT_TOOLS, notesFromAugment } from "../src/model/events";
import { initialSession, reduce, type MarginNote, type SessionState } from "../src/model/session";
import { marginLine } from "../src/views/copy";
import { marginView, MAX_MARGIN_LINES } from "../src/views/margin";
import { fixture, golden } from "./fake-host";

interface RecordedEvent {
  tool_name: string;
  tool_use_id: string;
  tool_input: unknown;
  result: { additionalContext?: string[] };
}

/**
 * A real session on a copy of this repository (one of the fastest-moving
 * indexed repos: hundreds of files fixed in the last 6 months, hundreds of
 * mined decisions): 20 of Claude's tool calls (8 edits of bug-fix-heavy and
 * decision-governed files) and Lens's own 8 lookups, recorded from
 * `classic.PostToolUse` with the plugin's augment hook running.
 */
const SESSION = JSON.parse(fixture("replay/repowise-edit-session.json")) as RecordedEvent[];

/** Replays the session through the same steps register.ts takes. */
function replay(events: RecordedEvent[]): SessionState {
  let state = initialSession;
  for (const e of events) {
    if (!EDIT_TOOLS.has(e.tool_name) || e.tool_use_id.startsWith("toolu_plugin_")) continue;
    state = reduce(state, { type: "notesFor", id: e.tool_use_id, notes: notesFromAugment(e.result.additionalContext) });
  }
  return state;
}

describe("margin notes over a recorded session", () => {
  it("is the session it claims to be", () => {
    const claude = SESSION.filter((e) => !e.tool_use_id.startsWith("toolu_plugin_"));
    expect(claude).toHaveLength(20);
    expect(claude.filter((e) => EDIT_TOOLS.has(e.tool_name))).toHaveLength(8);
    expect(SESSION.filter((e) => e.tool_use_id.startsWith("toolu_plugin_"))).toHaveLength(8);
  });

  it("shows at most three notes, each under the edit the hook flagged", () => {
    const state = replay(SESSION);
    const flagged = Object.keys(state.notes);
    expect(flagged.length).toBeLessThanOrEqual(3);
    expect(flagged).toEqual(["toolu_01P77H3c1LrnnZBi8MTE2LvR", "toolu_01RQU8ACcXBE7y2MqQFUiYXW", "toolu_018jYGKirRfgW3qFpfn18zGJ"]);
    for (const id of flagged) {
      const e = SESSION.find((x) => x.tool_use_id === id)!;
      expect(e.tool_name).toBe("Edit");
      expect(e.result.additionalContext?.length).toBeGreaterThan(0);
    }
  });

  it("draws exactly what the hook emitted, in Lens's words", () => {
    const state = replay(SESSION);
    const drawn = Object.values(state.notes).map((notes) => notes.map(marginLine));
    expect(drawn).toEqual([
      [
        "a decision found in this file, not yet reviewed: Configure languages declaratively in queries and language configs",
        "fixed 42 times in 6 months, most recently today, mostly in parse_file",
      ],
      [
        "a decision found in this file, not yet reviewed: Propagate maximum complexity to symbols",
        "fixed 39 times in 6 months, most recently today, mostly in analyze",
      ],
      [
        "a decision found in this file, not yet reviewed: Share one identifier shape for absence scans",
        "fixed 53 times in 6 months, most recently today, mostly in _member_is_used",
      ],
    ]);
  });

  it("ignores Lens's own lookups even when a hook adds context to them", () => {
    const own = SESSION.find((e) => e.tool_use_id.startsWith("toolu_plugin_"))!;
    const loud = { ...own, tool_name: "Edit", result: SESSION.find((e) => e.result.additionalContext)!.result };
    expect(Object.keys(replay([loud]).notes)).toEqual([]);
  });
});

describe("notesFromAugment", () => {
  it("reads every notice the hook's real formatters write (golden lines shared with the Python test)", () => {
    const [standingLine, minedLine, fixLine] = golden("augment-edit-notices.txt").split("\n").map((l) => l.trimEnd());
    expect(notesFromAugment([standingLine])).toEqual([{ kind: "decision", reviewed: true, title: "Use JWT auth" }]);
    expect(notesFromAugment([minedLine])).toEqual([
      { kind: "decision", reviewed: false, title: "Keep the parser allocation-free" },
    ]);
    expect(notesFromAugment([fixLine])).toEqual([{ kind: "fixes", count: 5, age: "2 weeks ago", symbol: "run_pipeline" }]);
  });

  const standing = "[repowise] a.py is governed by a standing decision: Keep the cache bounded because memory is finite (confirmed across 3 sessions).";

  it("reads a standing decision without its rationale or confirmation tail", () => {
    expect(notesFromAugment([standing])).toEqual([{ kind: "decision", reviewed: true, title: "Keep the cache bounded" }]);
  });

  it("reads a fix history with no symbol and no bug-magnet tail", () => {
    expect(notesFromAugment(["[repowise] a.py has been bug-fixed 3x in the last 6 months, last 2 weeks ago."])).toEqual([
      { kind: "fixes", count: 3, age: "2 weeks ago", symbol: null },
    ]);
  });

  it("keeps one note of each kind, across every hook's entry", () => {
    const fix = "[repowise] a.py has been bug-fixed 4x in the last 6 months, last yesterday; mostly in f.";
    const notes = notesFromAugment([standing, `${fix}\r\n${standing}`, fix]);
    expect(notes.map((n) => n.kind)).toEqual(["decision", "fixes"]);
  });

  it("ignores every other notice and anything that is not a hook's text", () => {
    const skeleton = "[repowise] You have only seen a.py as a skeleton this session — its bodies were elided.";
    expect(notesFromAugment([skeleton, "unrelated hook output", 42])).toEqual([]);
    expect(notesFromAugment(undefined)).toEqual([]);
    expect(notesFromAugment("[repowise] a.py has been bug-fixed 3x in the last 6 months, last today.")).toEqual([]);
  });
});

describe("margin view", () => {
  const decision: MarginNote = { kind: "decision", reviewed: true, title: "Keep the cache bounded" };
  const fixes: MarginNote = { kind: "fixes", count: 1, age: "yesterday", symbol: null };

  it("frames history as help, always with its age", () => {
    expect(marginLine(decision)).toBe("a standing decision covers this file: Keep the cache bounded");
    expect(marginLine(fixes)).toBe("fixed 1 time in 6 months, most recently yesterday");
  });

  it("is nothing without notes, and at most two dim lines", () => {
    expect(marginView(undefined)).toBeNull();
    expect(marginView([])).toBeNull();
    const tree = marginView([decision, fixes, decision]);
    expect(tree).toEqual({
      type: "Box",
      props: { key: "lens-margin", flexDirection: "column" },
      children: [
        { type: "Text", props: { dimColor: true, wrap: "truncate-end" }, children: [`  ${marginLine(decision)}`] },
        { type: "Text", props: { dimColor: true, wrap: "truncate-end" }, children: [`  ${marginLine(fixes)}`] },
      ],
    });
    expect(MAX_MARGIN_LINES).toBe(2);
  });

  it("an edit with nothing flagged leaves the state as it was", () => {
    expect(reduce(initialSession, { type: "notesFor", id: "x", notes: [] })).toBe(initialSession);
  });
});
