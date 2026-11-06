import { describe, expect, it } from "vitest";

import { areaOf, dirOf, groupByArea, inArea, separatingDepth } from "../../src/lib/areas";

describe("areas", () => {
  it("names a file's area at a depth, root files in the root area", () => {
    expect(dirOf("src/a/b.py")).toBe("src/a");
    expect(areaOf("src/a/b/c.py", 2)).toBe("src/a");
    expect(areaOf("src/a/b/c.py", 9)).toBe("src/a/b");
    expect(areaOf("README.md", 2)).toBe("");
  });

  it("separates one level below the directory every path shares", () => {
    expect(separatingDepth(["packages/core/x.py", "packages/server/y.py"])).toBe(2);
    expect(separatingDepth(["src/a/x.py", "lib/y.py"])).toBe(1);
    expect(separatingDepth(["a/b/c/d/e/f.py", "a/b/c/d/e/g.py"], 4)).toBe(4);
    expect(separatingDepth([])).toBe(1);
  });

  it("groups in first-seen order and keeps each group's order", () => {
    const groups = groupByArea(["src/b/1", "src/a/2", "src/b/3"], (p) => p, 2);
    expect(groups).toEqual([
      { area: "src/b", items: ["src/b/1", "src/b/3"] },
      { area: "src/a", items: ["src/a/2"] },
    ]);
  });

  it("holds a path inside its area at any depth, never a sibling with a shared prefix", () => {
    expect(inArea("src/a/b/c.py", "src/a")).toBe(true);
    expect(inArea("src/ab/c.py", "src/a")).toBe(false);
    // The root area is the files at the root, as groupByArea groups them.
    expect(inArea("x.py", "")).toBe(true);
    expect(inArea("src/x.py", "")).toBe(false);
  });
});
