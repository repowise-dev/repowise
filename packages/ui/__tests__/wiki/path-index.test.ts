import { describe, it, expect } from "vitest";
import {
  buildPathIndex,
  elidePath,
  looksLikePath,
  normalizePath,
  resolvePath,
} from "../../src/wiki/path-index";

const page = (id: string, target_path: string) => ({ id, target_path });

describe("looksLikePath", () => {
  it("accepts paths and filenames with extensions", () => {
    expect(looksLikePath("packages/core/fs_walk.py")).toBe(true);
    expect(looksLikePath("languages/spec.py")).toBe(true);
    expect(looksLikePath("spec.py")).toBe(true);
    expect(looksLikePath("index.ts")).toBe(true);
  });

  it("rejects prose shorthand that inline code is also used for", () => {
    // Bare words are identifiers, not paths.
    expect(looksLikePath("chat")).toBe(false);
    expect(looksLikePath("ParsedFile")).toBe(false);
    // A leading dot with no basename in front of it is a technology name.
    expect(looksLikePath(".NET")).toBe(false);
    // Anything with whitespace is a phrase.
    expect(looksLikePath("npm run build")).toBe(false);
    expect(looksLikePath("")).toBe(false);
  });
});

describe("normalizePath", () => {
  it("strips the decorations prose adds to a path", () => {
    expect(normalizePath("./packages/ui")).toBe("packages/ui");
    expect(normalizePath("packages/ui/")).toBe("packages/ui");
    expect(normalizePath("packages/ui/src/*")).toBe("packages/ui/src");
    expect(normalizePath("  packages/ui  ")).toBe("packages/ui");
    expect(normalizePath("packages//ui")).toBe("packages/ui");
  });
});

describe("buildPathIndex / resolvePath", () => {
  const pages = [
    page("p1", "packages/core/src/repowise/core/fs_walk.py"),
    page("p2", "packages/core/src/repowise/core/ingestion/models.py"),
    page("p3", "packages/core/src/repowise/core/persistence/models.py"),
    page("p4", "packages/ui"),
  ];
  const index = buildPathIndex(pages);

  it("resolves a full path", () => {
    expect(resolvePath(index, "packages/core/src/repowise/core/fs_walk.py")).toEqual({
      pageId: "p1",
      path: "packages/core/src/repowise/core/fs_walk.py",
    });
  });

  it("resolves an unambiguous trailing fragment", () => {
    expect(resolvePath(index, "core/fs_walk.py")?.pageId).toBe("p1");
    expect(resolvePath(index, "fs_walk.py")?.pageId).toBe("p1");
    expect(resolvePath(index, "ingestion/models.py")?.pageId).toBe("p2");
  });

  it("refuses to guess when a fragment is ambiguous", () => {
    // Two pages end in models.py. A wrong link is indistinguishable from a
    // right one until you follow it, so neither wins.
    expect(resolvePath(index, "models.py")).toBeNull();
  });

  it("keeps an exact path even when it is also an ambiguous suffix", () => {
    const withCollision = buildPathIndex([
      page("a", "models.py"),
      page("b", "deep/nested/models.py"),
    ]);
    expect(resolvePath(withCollision, "models.py")?.pageId).toBe("a");
  });

  it("normalizes the anchor before lookup", () => {
    expect(resolvePath(index, "./packages/ui/")?.pageId).toBe("p4");
    expect(resolvePath(index, "packages/ui/*")?.pageId).toBe("p4");
  });

  it("returns null for unknown paths and for non-paths", () => {
    expect(resolvePath(index, "packages/nope/missing.py")).toBeNull();
    expect(resolvePath(index, "chat")).toBeNull();
  });

  it("ignores symbol targets, which the interlinker owns", () => {
    const symbols = buildPathIndex([page("s", "packages/core/engine.py::Engine")]);
    expect(resolvePath(symbols, "packages/core/engine.py")).toBeNull();
  });

  it("tolerates pages with no target path", () => {
    const messy = buildPathIndex([
      { id: "x" },
      { id: "y", target_path: null },
      { id: "z", target_path: "" },
      page("ok", "a/b.py"),
    ]);
    expect(resolvePath(messy, "a/b.py")?.pageId).toBe("ok");
  });

  it("indexes only the last few segments, so a deep prefix does not resolve", () => {
    const deep = buildPathIndex([page("d", "a/b/c/d/e/f.py")]);
    expect(resolvePath(deep, "d/e/f.py")?.pageId).toBe("d");
    // Six segments from the end is beyond what prose writes and beyond what
    // we index; the full path still works.
    expect(resolvePath(deep, "b/c/d/e/f.py")).toBeNull();
    expect(resolvePath(deep, "a/b/c/d/e/f.py")?.pageId).toBe("d");
  });
});

describe("elidePath", () => {
  it("leaves a short path alone", () => {
    expect(elidePath("packages/ui")).toEqual({ head: "", tail: "packages/ui" });
  });

  it("keeps the tail of a long path and marks the head for dimming", () => {
    const { head, tail } = elidePath(
      "packages/core/src/repowise/core/ingestion/models.py",
    );
    expect(tail).toBe("ingestion/models.py");
    expect(head).toBe("packages/…/");
  });

  it("does not elide a long path that has nothing to drop", () => {
    const single = "a-very-long-single-segment-filename-indeed.py";
    expect(elidePath(single)).toEqual({ head: "", tail: single });
  });
});
