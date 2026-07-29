import { describe, it, expect } from "vitest";
import { moduleGroupFor } from "../../src/graph/use-module-filter.js";

describe("moduleGroupFor", () => {
  it("groups on two path segments, not the top-level directory", () => {
    expect(moduleGroupFor("packages/core/src/repowise/core/pipeline/persist.py")).toBe(
      "packages/core",
    );
    expect(moduleGroupFor("packages/ui/src/graph/graph-flow.tsx")).toBe("packages/ui");
    expect(moduleGroupFor("tests/unit/server/test_graph.py")).toBe("tests/unit");
  });

  it("takes the second segment only when it is a directory", () => {
    // Otherwise `tests/conftest.py` invents a one-file module sitting beside
    // the real `tests/unit`.
    expect(moduleGroupFor("tests/conftest.py")).toBe("tests");
    expect(moduleGroupFor("scripts/build.mjs")).toBe("scripts");
  });

  it("buckets root-level files and every flavour of external", () => {
    expect(moduleGroupFor("README.md")).toBe("(repo root)");
    expect(moduleGroupFor("external:next/navigation")).toBe("external");
    expect(moduleGroupFor("external:@testing-library/react")).toBe("external");
    // `framework:` ids are third-party too — the same trap `isExternalModuleId`
    // exists to close.
    expect(moduleGroupFor("framework:django/db")).toBe("external");
  });

  it("partitions this repo instead of dimming a sliver of it", () => {
    // The whole reason the Modules *scope* was replaced by a filter: on the
    // real export, `packages/` alone holds 92% of the files, so a top-level
    // grouping would offer a "filter" that dims 8% of the canvas. Two segments
    // split the same files into groups no single one of which dominates.
    const paths = [
      ...Array.from({ length: 575 }, (_, i) => `packages/core/src/a${i}.py`),
      ...Array.from({ length: 307 }, (_, i) => `packages/ui/src/b${i}.tsx`),
      ...Array.from({ length: 138 }, (_, i) => `packages/server/src/c${i}.py`),
      ...Array.from({ length: 127 }, (_, i) => `tests/unit/d${i}.py`),
    ];
    const counts = new Map<string, number>();
    for (const p of paths) {
      const g = moduleGroupFor(p);
      counts.set(g, (counts.get(g) ?? 0) + 1);
    }

    expect([...counts.keys()].sort()).toEqual([
      "packages/core",
      "packages/server",
      "packages/ui",
      "tests/unit",
    ]);
    const largest = Math.max(...counts.values()) / paths.length;
    expect(largest).toBeLessThan(0.6);
  });
});
