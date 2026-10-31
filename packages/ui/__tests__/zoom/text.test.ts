import { describe, expect, it } from "vitest";

import {
  endEllipsis,
  middleEllipsis,
  TextLayoutCache,
  tokenize,
  wrapText,
} from "../../src/zoom/text";

/** Monospace stand-in for measureText: every char is 6px wide. */
const mono = (s: string): number => s.length * 6;

/** A stub context that counts measureText calls. */
function countingCtx() {
  const ctx = {
    font: "",
    calls: 0,
    measureText(s: string) {
      ctx.calls++;
      return { width: mono(s) };
    },
  };
  return ctx;
}

describe("wrapText (words)", () => {
  it("keeps a name that fits on one line", () => {
    expect(wrapText("Documentation", 100, 2, "words", mono)).toEqual(["Documentation"]);
  });

  it("wraps to two lines before ellipsizing", () => {
    // 72px = 12 chars per line.
    expect(wrapText("Command Line Interface", 72, 2, "words", mono)).toEqual([
      "Command Line",
      "Interface",
    ]);
  });

  it("ellipsizes the end of the last allowed line", () => {
    const lines = wrapText("one two three four five six seven", 60, 2, "words", mono);
    expect(lines).toHaveLength(2);
    expect(lines[0]).toBe("one two");
    expect(lines[1]!.endsWith("…")).toBe(true);
    for (const l of lines) expect(mono(l)).toBeLessThanOrEqual(60);
  });

  it("ellipsizes a word wider than the line instead of breaking it", () => {
    expect(wrapText("Infrastructure", 48, 2, "words", mono)).toEqual(["Infrast…"]);
    expect(wrapText("Big Infrastructure", 48, 2, "words", mono)).toEqual(["Big", "Infrast…"]);
  });

  it("returns nothing when no line fits", () => {
    expect(wrapText("abc", 0, 2, "words", mono)).toEqual([]);
    expect(wrapText("abc", 60, 0, "words", mono)).toEqual([]);
    expect(wrapText("   ", 60, 2, "words", mono)).toEqual([]);
  });
});

describe("wrapText (path)", () => {
  it("never splits a path segment across lines", () => {
    expect(wrapText("hooks.py", 30, 2, "path", mono)).toEqual(["h….py"]);
  });

  it("breaks after separators and keeps the tail of the overflow", () => {
    const lines = wrapText("very-long-component-name-zoom-detail-panel.tsx", 66, 2, "path", mono);
    expect(lines).toHaveLength(2);
    expect(lines[0]).toBe("very-long-");
    expect(lines[1]).toContain("…");
    expect(lines[1]!.startsWith("comp")).toBe(true);
    expect(lines[1]!.endsWith(".tsx")).toBe(true);
    for (const l of lines) expect(mono(l)).toBeLessThanOrEqual(66);
  });

  it("uses a middle ellipsis on a single line", () => {
    const [line] = wrapText("zoom-detail-panel.tsx", 102, 1, "path", mono);
    expect(line).toBe("zoom-de…panel.tsx");
  });

  it("never splits a surrogate pair", () => {
    const name = "\u{1F680}\u{1F680}\u{1F680}\u{1F680}launch.ts";
    const lone = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
    for (const w of [12, 18, 30, 42, 54]) {
      expect(endEllipsis(name, w, mono)).not.toMatch(lone);
      expect(middleEllipsis(name, w, mono)).not.toMatch(lone);
    }
  });
});

describe("ellipsis helpers", () => {
  it("endEllipsis leaves fitting text alone and cuts the rest", () => {
    expect(endEllipsis("short", 60, mono)).toBe("short");
    expect(endEllipsis("Documentation", 48, mono)).toBe("Documen…");
    expect(endEllipsis("abc", 3, mono)).toBe("");
  });

  it("middleEllipsis keeps the extension", () => {
    const out = middleEllipsis("relation-summary.test.ts", 72, mono);
    expect(mono(out)).toBeLessThanOrEqual(72);
    expect(out.endsWith("test.ts")).toBe(true);
    expect(out.startsWith("rel")).toBe(true);
  });

  it("tokenize splits paths after separators", () => {
    expect(tokenize("a-b_c.ts", "path")).toEqual(["a-", "b_", "c.ts"]);
    expect(tokenize("app.config.ts", "path")).toEqual(["app.", "config.ts"]);
    expect(tokenize("__init__.py", "path")).toEqual(["_", "_", "init_", "_", ".py"]);
    expect(tokenize(".env", "path")).toEqual([".env"]);
    expect(tokenize(" a  b ", "words")).toEqual(["a", "b"]);
  });

  it("tokenize path edge cases", () => {
    expect(tokenize("", "path")).toEqual([]);
    expect(tokenize("   ", "path")).toEqual([]);
    expect(tokenize("a/b/", "path")).toEqual(["a/", "b/"]);
    expect(tokenize("-a", "path")).toEqual(["-", "a"]);
    expect(tokenize("a--b", "path")).toEqual(["a-", "-", "b"]);
    expect(tokenize("a.", "path")).toEqual(["a."]);
    expect(tokenize("a..b", "path")).toEqual(["a.", ".b"]);
    expect(tokenize("a 	 b", "path")).toEqual(["a ", "b"]);
    expect(tokenize("fooBar2Baz", "path")).toEqual(["fooBar2Baz"]);
  });
});

describe("TextLayoutCache", () => {
  const FONT = "600 12px sans-serif";

  it("does no measuring on a repeat frame", () => {
    const cache = new TextLayoutCache();
    const ctx = countingCtx();
    const names = ["Command Line Interface", "Documentation", "zoom-detail-panel.tsx"];
    const frame = (): void => {
      for (const n of names) cache.lines(ctx, FONT, n, 80, 2, "words");
    };
    frame();
    const first = ctx.calls;
    expect(first).toBeGreaterThan(0);
    frame();
    expect(ctx.calls).toBe(first);
  });

  it("reuses token widths across width buckets during a zoom", () => {
    const cache = new TextLayoutCache();
    const ctx = countingCtx();
    cache.lines(ctx, FONT, "alpha beta gamma delta", 60, 2, "words");
    const after = ctx.calls;
    cache.lines(ctx, FONT, "alpha beta gamma delta", 200, 2, "words");
    // Every token was measured already; the wider wrap measures nothing new.
    expect(ctx.calls).toBe(after);
  });

  it("returns the same lines as wrapText, for the bucketed width", () => {
    const cache = new TextLayoutCache();
    const ctx = countingCtx();
    expect(cache.lines(ctx, FONT, "Command Line Interface", 75, 2, "words")).toEqual(
      wrapText("Command Line Interface", 72, 2, "words", mono),
    );
  });

  it("stays bounded", () => {
    const cache = new TextLayoutCache(3);
    const ctx = countingCtx();
    for (const t of ["a", "b", "c", "d"]) cache.lines(ctx, FONT, t, 80, 1, "words");
    // Past the limit the cache was dropped, so "a" is measured again.
    const before = ctx.calls;
    cache.lines(ctx, FONT, "a", 80, 1, "words");
    expect(ctx.calls).toBeGreaterThan(before);
  });

  it("measures each character once, whatever strings it appears in", () => {
    const cache = new TextLayoutCache();
    const ctx = countingCtx();
    cache.lines(ctx, FONT, "abc abc", 80, 1, "words");
    expect(ctx.calls).toBe(4); // a, b, c and the space
    cache.lines(ctx, FONT, "cab bca", 80, 2, "words");
    expect(ctx.calls).toBe(4);
  });
});
