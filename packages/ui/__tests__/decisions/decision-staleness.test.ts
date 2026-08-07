import { describe, it, expect } from "vitest";
import {
  describeStaleness,
  describeRecordStaleness,
} from "../../src/decisions/decision-staleness.js";

describe("describeStaleness", () => {
  it("separates unscoped from unchanged, which both score zero", () => {
    // The whole reason this helper exists. Both used to render one em dash.
    const unscoped = describeStaleness([], 0);
    const unchanged = describeStaleness(["a.ts", "b.ts"], 0);
    expect(unscoped.kind).toBe("unscoped");
    expect(unchanged.kind).toBe("unchanged");
    expect(unscoped.short).not.toBe(unchanged.short);
    expect(unscoped.sentence).not.toBe(unchanged.sentence);
  });

  it("treats a null scope as unscoped rather than as an empty repository", () => {
    expect(describeStaleness(null, 0).kind).toBe("unscoped");
    expect(describeStaleness(undefined, 0.5).kind).toBe("unscoped");
  });

  it("converts the proportion back into the count it is a proportion of", () => {
    // 0.3 x 7 = 2.1, which round and ceil disagree about, so this pins round.
    const s7 = describeStaleness(["a", "b", "c", "d", "e", "f", "g"], 0.3);
    expect(s7.changedCount).toBe(2);
    const s = describeStaleness(["a", "b", "c", "d", "e", "f", "g", "h"], 0.25);
    expect(s.kind).toBe("moved");
    expect(s.changedCount).toBe(2);
    expect(s.fileCount).toBe(8);
    expect(s.short).toBe("2 of 8");
  });

  it("never reports zero changed under a non-zero score", () => {
    // One file of 300 rounds to zero, and "0 of 300 have changed" printed
    // under a score the server says is non-zero costs the reader their trust
    // in every other number on the page.
    const wide = Array.from({ length: 300 }, (_, i) => `f${i}.ts`);
    const s = describeStaleness(wide, 0.001);
    expect(s.kind).toBe("moved");
    expect(s.changedCount).toBe(1);
  });

  it("never reports more changed than the record names", () => {
    // 1.4 does not exercise the clamp: round(1.4 * 1) is 1 already. A score
    // above 2 is what reaches it.
    const s = describeStaleness(["a.ts"], 2.6);
    expect(s.changedCount).toBe(1);
    expect(s.short).toBe("1 of 1");
    const wide = describeStaleness(["a.ts", "b.ts"], 5);
    expect(wide.changedCount).toBe(2);
  });

  it("gives one file its own sentence instead of a plural with the s removed", () => {
    expect(describeStaleness(["a.ts"], 0).sentence).toBe(
      "The one file it names is still tracked and unchanged since it was recorded.",
    );
    expect(describeStaleness(["a.ts"], 1).sentence).toContain(
      "The one file it names has changed",
    );
    expect(describeStaleness(["a.ts", "b.ts"], 1).sentence).toContain(
      "2 of its 2 files have changed"
    );
    // Sweep the small cases for disagreement. "1 of its 2 files has changed"
    // is correct and must not be flagged: the subject is the count, not the
    // scope, so a bare /files has/ ban is wrong and this test failed on it
    // once already.
    for (const n of [1, 2, 3]) {
      for (const score of [0, 0.5, 1]) {
        const s = describeStaleness(
          Array.from({ length: n }, (_, i) => `f${i}.ts`),
          score,
        ).sentence;
        expect(s).not.toMatch(/\b1 files\b/);
        expect(s).not.toMatch(/\b[2-9]\d* of its \d+ files has\b/);
        expect(s).not.toMatch(/\b1 of its \d+ files have\b/);
      }
    }
  });

  it("survives a non-finite score instead of printing NaN", () => {
    expect(describeStaleness(["a.ts"], Number.NaN).kind).toBe("unchanged");
    expect(describeStaleness(["a.ts"], null).kind).toBe("unchanged");
    expect(describeStaleness(["a.ts"], undefined).short).toBe("0 of 1");
  });

  it("refuses to vouch for a status nobody rescores", () => {
    // `recompute_decision_staleness` only touches active and proposed, and the
    // column defaults to 0.0, so a deprecated record would otherwise report
    // "all 2 files unchanged" from a value never computed.
    const dep = describeRecordStaleness({
      affected_files: ["a.ts", "b.ts"],
      staleness_score: 0,
      status: "deprecated",
    });
    expect(dep.kind).toBe("unscored");
    expect(dep.short).toBe("not scored");
    expect(dep.sentence).not.toMatch(/unchanged/);

    for (const status of ["active", "proposed"] as const) {
      expect(
        describeRecordStaleness({
          affected_files: ["a.ts", "b.ts"],
          staleness_score: 0,
          status,
        }).kind,
      ).toBe("unchanged");
    }
  });

  it("reads the two fields off a whole record", () => {
    expect(
      describeRecordStaleness({
        affected_files: ["a.ts", "b.ts", "c.ts", "d.ts"],
        staleness_score: 0.5,
        status: "active",
      }).short,
    ).toBe("2 of 4");
  });
});
