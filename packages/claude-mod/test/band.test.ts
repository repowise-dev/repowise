import { describe, expect, it } from "vitest";
import { countOf, fit } from "../src/format";
import { initialSession, type SessionState } from "../src/model/session";
import { bandRows, bandView, MAX_BAND_ROWS } from "../src/views/band";
import { HINTS, freshnessLine } from "../src/views/copy";
import { box, materialize, text, type Node } from "../src/views/elements";

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
    expect(lines(bandView(behind, at(100)))).toEqual(["index 14 files behind HEAD · repowise update"]);
  });

  it.each(Object.entries(HINTS))("shows the %s hint as copyable text", (kind, copy) => {
    const s = { ...initialSession, hint: kind, hintsShown: [kind] } as SessionState;
    expect(lines(bandView(s, at(180)))).toEqual([copy]);
  });

  it("puts the hint above freshness, never more than two rows", () => {
    expect(lines(bandView(both, at(180)))).toEqual([HINTS["no-server"], "index 1 file behind HEAD · repowise update"]);
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
    const long: SessionState = { ...initialSession, hint: "auth", hintsShown: ["auth"] };
    const [narrow] = lines(bandView(long, at(60)));
    expect(HINTS.auth.length).toBeGreaterThan(60);
    expect(narrow?.length).toBeGreaterThan(55);
    expect(narrow?.length).toBeLessThanOrEqual(60);
    expect(narrow?.endsWith("…")).toBe(true);
    expect(lines(bandView(long, at(100)))).toEqual([HINTS.auth]);
    expect(lines(bandView(long, at(180)))).toEqual([HINTS.auth]);
  });

  it("uses no em dashes in any copy", () => {
    for (const copy of [...Object.values(HINTS), freshnessLine({ changedFiles: 2 })]) expect(copy).not.toContain("—");
  });
});

describe("freshness copy", () => {
  it("counts files with a unit, singular and plural, with separators", () => {
    expect(freshnessLine({ changedFiles: 1 })).toBe("index 1 file behind HEAD · repowise update");
    expect(freshnessLine({ changedFiles: 1204 })).toBe("index 1,204 files behind HEAD · repowise update");
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
