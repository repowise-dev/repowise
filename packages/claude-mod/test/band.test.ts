import { describe, expect, it } from "vitest";
import type { Savings } from "@repowise-dev/api-client/costs";
import { countOf, fit } from "../src/format";
import { savingsSince, savingsTotals } from "../src/model/events";
import { initialSession, reduce, type SavingsDelta, type SessionState } from "../src/model/session";
import { bandRows, bandView, MAX_BAND_ROWS } from "../src/views/band";
import { HINTS, freshnessLine, savingsLine } from "../src/views/copy";
import { box, materialize, text, type Node } from "../src/views/elements";
import { fixture } from "./fake-host";

const at = (columns: number) => ({ columns, hasSurvey: false });

function lines(node: Node | null): string[] {
  if (node === null) return [];
  if (node.type === "Text") return node.children;
  return node.children.flatMap(lines);
}

const behind: SessionState = { ...initialSession, mode: "full", freshness: { changedFiles: 14 } };
const hinted: SessionState = { ...initialSession, mode: "lite", hint: "no-server", hintsShown: ["no-server"] };
const both: SessionState = { ...hinted, freshness: { changedFiles: 1 } };

describe("band", () => {
  it("draws nothing at rest", () => {
    expect(bandView(initialSession, at(100))).toBeNull();
    expect(bandView({ ...initialSession, mode: "full" }, at(100))).toBeNull();
  });

  it("draws nothing once a hint has retired and the index is current", () => {
    expect(bandView({ ...hinted, hint: null }, at(100))).toBeNull();
  });

  it("shows the freshness exception", () => {
    expect(lines(bandView(behind, at(100)))).toEqual(["index behind HEAD (14 files changed) · repowise update"]);
  });

  it.each(Object.entries(HINTS))("shows the %s hint as copyable text", (kind, copy) => {
    const s = { ...initialSession, hint: kind, hintsShown: [kind] } as SessionState;
    expect(lines(bandView(s, at(180)))).toEqual([copy]);
  });

  it("puts the hint above freshness, never more than two rows", () => {
    expect(lines(bandView(both, at(180)))).toEqual([HINTS["no-server"], "index behind HEAD (1 file changed) · repowise update"]);
    expect(bandRows(both).length).toBeLessThanOrEqual(MAX_BAND_ROWS);
  });

  it("yields to a survey", () => {
    expect(bandView(both, { columns: 100, hasSurvey: true })).toBeNull();
  });

  it("is quiet text: dim, truncating, in one keyed column", () => {
    const node = bandView(both, at(100));
    expect(node).toMatchObject({ type: "Box", props: { key: "lens-band", flexDirection: "column" } });
    for (const row of (node as Extract<Node, { type: "Box" }>).children) {
      expect(row).toMatchObject({ type: "Text", props: { dimColor: true, wrap: "truncate-end" } });
    }
  });

  it.each([60, 100, 180])("fits every row in %i columns", (columns) => {
    for (const row of lines(bandView(both, at(columns)))) expect(row.length).toBeLessThanOrEqual(columns);
  });

  it("truncates a long row at 60 columns with an ellipsis and keeps it whole at 100 and 180", () => {
    const long: SessionState = { ...initialSession, hint: "unlisted", hintsShown: ["unlisted"] };
    const [narrow] = lines(bandView(long, at(60)));
    expect(HINTS.unlisted.length).toBeGreaterThan(60);
    expect(narrow?.length).toBeGreaterThan(55);
    expect(narrow?.length).toBeLessThanOrEqual(60);
    expect(narrow?.endsWith("…")).toBe(true);
    expect(lines(bandView(long, at(100)))).toEqual([HINTS.unlisted]);
    expect(lines(bandView(long, at(180)))).toEqual([HINTS.unlisted]);
  });

  it("uses no em dashes in any copy", () => {
    for (const copy of [...Object.values(HINTS), freshnessLine({ changedFiles: 2 })]) expect(copy).not.toContain("—");
  });
});

describe("freshness copy", () => {
  it("counts files with a unit, singular and plural, with separators", () => {
    expect(freshnessLine({ changedFiles: 1 })).toBe("index behind HEAD (1 file changed) · repowise update");
    expect(freshnessLine({ changedFiles: 1204 })).toBe("index behind HEAD (1,204 files changed) · repowise update");
  });

  it("states no number it did not measure", () => {
    expect(freshnessLine({ changedFiles: null })).toBe("index behind HEAD · repowise update");
  });
});

describe("format", () => {
  it("countOf", () => {
    expect(countOf(0, "file", "files")).toBe("0 files");
  });

  it("fit leaves short text alone and handles a zero width", () => {
    expect(fit("abc", 3)).toBe("abc");
    expect(fit("abcd", 3)).toBe("ab…");
    expect(fit("abc", 0)).toBe("");
  });
});

describe("materialize", () => {
  it("builds the tree through the surface's element table", () => {
    const table = {
      Box: (props: Record<string, unknown>) => ({ el: "Box", props }),
      Text: (props: Record<string, unknown>) => ({ el: "Text", props }),
    };
    const out = materialize(box({ key: "k" }, [text("hi", { dimColor: true })]), table);
    expect(out).toEqual({
      el: "Box",
      props: { key: "k", children: [{ el: "Text", props: { dimColor: true, children: ["hi"] } }] },
    });
  });

  it("refuses an element the surface does not have", () => {
    expect(() => materialize(text("hi"), {})).toThrow(/Text/);
  });
});

describe("savings row", () => {
  // Real ledger reads from a local server on the requests index, before and
  // after one `repowise distill git log -n 300`.
  const before = savingsTotals(JSON.parse(fixture("savings/before-distill.json")) as Savings);
  const after = savingsTotals(JSON.parse(fixture("savings/after-distill.json")) as Savings);
  const measured = savingsSince(after, before)!;
  const big: SavingsDelta = { tokens: 182_400, inferredTokens: 0, usd: 2.41 };
  const withInferred: SavingsDelta = { ...big, inferredTokens: 12_000 };
  const saving = (delta: SavingsDelta, s: SessionState = initialSession) => reduce(s, { type: "savings", delta });

  it("reads the delta from the server's own figures", () => {
    expect(before).toEqual({ tokens: 0, inferredTokens: 0, usd: 0 });
    expect(measured).toEqual({ tokens: 7099, inferredTokens: 0, usd: 0.035495 });
    // The ledger only grows: a drop means a rebuilt ledger, and the snapshot no longer holds.
    expect(savingsSince(before, after)).toBeNull();
    expect(savingsSince({ ...after, usd: 0 }, after)).toBeNull();
    expect(savingsSince({ tokens: 10, inferredTokens: 50, usd: 0 }, before)).toEqual({ tokens: 10, inferredTokens: 10, usd: 0 });
  });

  it("renders nothing at rest and nothing for a zero delta", () => {
    expect(bandView(saving({ tokens: 0, inferredTokens: 0, usd: 0 }), at(100))).toBeNull();
    // A zero delta is never a row, even after one.
    expect(saving({ tokens: 0, inferredTokens: 0, usd: 0 }, saving(big)).savings).toBeNull();
  });

  it("names the scope: since this session started, every agent on this repo", () => {
    expect(lines(bandView(saving(measured), at(100)))).toEqual([
      "7,099 tokens · $0.04 saved since this session started · all agents on this repo",
    ]);
    expect(lines(bandView(saving(big), at(180)))).toEqual([
      "182,400 tokens · $2.41 saved since this session started · all agents on this repo",
    ]);
  });

  it("says how much of it is inferred, and leaves out a dollar figure under a cent", () => {
    expect(savingsLine(withInferred, 180)).toBe(
      "182,400 tokens (12,000 inferred) · $2.41 saved since this session started · all agents on this repo",
    );
    expect(savingsLine({ tokens: 1, inferredTokens: 0, usd: 0.004 }, 180)).toBe(
      "1 token saved since this session started · all agents on this repo",
    );
  });

  it("keeps the scope at 60 columns with the short form", () => {
    expect(lines(bandView(saving(big), at(60)))).toEqual(["182,400 tokens · $2.41 saved this session · all agents"]);
  });

  it.each([60, 100, 180])("fits %i columns with every row showing", (columns) => {
    const all: SessionState = { ...saving(withInferred, both), hint: null };
    for (const row of lines(bandView(all, at(columns)))) expect(row.length).toBeLessThanOrEqual(columns);
  });

  it("comes after a hint and freshness, within two rows, and yields to a survey", () => {
    expect(bandRows(saving(big, both))).toEqual([HINTS["no-server"], "index behind HEAD (1 file changed) · repowise update"]);
    expect(bandRows(saving(big, behind))).toEqual([
      "index behind HEAD (14 files changed) · repowise update",
      "182,400 tokens · $2.41 saved since this session started · all agents on this repo",
    ]);
    expect(bandView(saving(big), { columns: 100, hasSurvey: true })).toBeNull();
  });

  it("leaves with the local server", () => {
    const full = reduce(saving(big), { type: "discovered", mode: "full", freshness: null });
    expect(full.savings).toEqual(big);
    expect(reduce(full, { type: "discovered", mode: "lite", freshness: null }).savings).toBeNull();
  });

  it("uses no em dashes", () => {
    expect(savingsLine(withInferred, 60)).not.toContain("—");
    expect(savingsLine(withInferred, 180)).not.toContain("—");
  });
});
