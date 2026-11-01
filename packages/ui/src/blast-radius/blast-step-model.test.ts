import { describe, expect, it } from "vitest";
import type { AffectedSignal, BlastRadiusResponse, TransitiveEntry } from "@repowise-dev/types/blast-radius";
import { readReach } from "./blast-map-model";
import {
  STEP_LAYOUT,
  TOP_FOLDERS,
  blockFocusPaths,
  buildStepMap,
  defaultStepBlock,
  stepFolderOf,
  stepWords,
} from "./blast-step-model";

function result(
  transitive: TransitiveEntry[],
  signals: AffectedSignal[] = [],
): BlastRadiusResponse & { affected_signals: AffectedSignal[] } {
  return {
    direct_risks: [],
    transitive_affected: transitive,
    cochange_warnings: [],
    recommended_reviewers: [],
    test_gaps: [],
    structural_impact_score: 1,
    structural_impact_band: "localized",
    structural_impact_scale: {} as BlastRadiusResponse["structural_impact_scale"],
    overall_risk_score: 1,
    overall_risk_score_compatibility: {} as BlastRadiusResponse["overall_risk_score_compatibility"],
    affected_signals: signals,
  };
}

const sig = (path: string, health_score: number | null, is_test = false): AffectedSignal => ({
  path,
  health_score,
  is_hotspot: false,
  module: null,
  is_test,
});

const CORE = "packages/core/src/repowise/core/persistence/a.py";

describe("stepFolderOf", () => {
  it("names the package and the first folder under its source root", () => {
    expect(stepFolderOf("packages/server/src/repowise/server/mcp_server/tools/x.py")).toBe("server/mcp_server");
    expect(stepFolderOf("packages/cli/src/repowise/cli/commands/x.py")).toBe("cli/commands");
    expect(stepFolderOf("packages/ui/src/blast-radius/x.tsx")).toBe("ui/blast-radius");
    expect(stepFolderOf("apps/web/src/app/page.tsx")).toBe("web/app");
  });

  it("calls a file at the package root the package's top", () => {
    expect(stepFolderOf("packages/server/src/repowise/server/app.py")).toBe("server (top)");
    expect(stepFolderOf("scripts/gen.py")).toBe("scripts (top)");
    expect(stepFolderOf("setup.py")).toBe("(root)");
  });
});

describe("buildStepMap", () => {
  it("groups each step by folder, sizes blocks by files and marks poor health", () => {
    const reach = readReach(
      result(
        [
          { path: "packages/server/src/repowise/server/routers/a.py", depth: 1, via: CORE },
          { path: "packages/server/src/repowise/server/routers/b.py", depth: 1, via: CORE },
          { path: "packages/cli/src/repowise/cli/commands/c.py", depth: 1, via: CORE },
          { path: "packages/cli/src/repowise/cli/commands/d.py", depth: 2, via: "packages/server/src/repowise/server/routers/a.py" },
        ],
        [sig("packages/server/src/repowise/server/routers/a.py", 3.2), sig("packages/server/src/repowise/server/routers/b.py", 8)],
      ),
      [CORE],
    );
    const map = buildStepMap(reach);
    const at = (c: number) => map.blocks.filter((b) => b.column === c).map((b) => [b.label, b.files, b.poor]);
    expect(at(0)).toEqual([["core/persistence", 1, 0]]);
    expect(at(1)).toEqual([
      ["server/routers", 2, 1],
      ["cli/commands", 1, 0],
    ]);
    expect(at(2)).toEqual([["cli/commands", 1, 0]]);
    expect(map.columns.map((c) => c.title)).toEqual(["Changed", "1 step", "2 steps", "3 steps"]);
    const server = map.blocks.find((b) => b.key === "1:server/routers")!;
    const cli = map.blocks.find((b) => b.key === "1:cli/commands")!;
    expect(server.height).toBeGreaterThan(cli.height);
  });

  it("never draws tests, and counts them apart", () => {
    const reach = readReach(
      result(
        [
          { path: "packages/core/src/repowise/core/x/b.py", depth: 1, via: CORE },
          { path: "tests/unit/test_a.py", depth: 1, via: CORE },
        ],
        [sig("tests/unit/test_a.py", 9, true)],
      ),
      [CORE, "tests/unit/test_core.py"],
    );
    const map = buildStepMap(reach);
    const drawn = map.blocks.flatMap((b) => b.paths);
    expect(drawn.some((p) => p.startsWith("tests/"))).toBe(false);
    expect(map.testsNotDrawn).toBe(1);
    expect(map.changedTestsNotDrawn).toBe(1);
  });

  it("keeps the top folders and folds the rest into one block", () => {
    const folders = TOP_FOLDERS + 3;
    const transitive = Array.from({ length: folders }, (_, i) =>
      Array.from({ length: folders - i }, (_, j) => ({
        path: `packages/p/src/f${i}/m${j}.py`,
        depth: 1,
        via: CORE,
      })),
    ).flat();
    const map = buildStepMap(readReach(result(transitive), [CORE]));
    const step1 = map.blocks.filter((b) => b.column === 1);
    expect(step1).toHaveLength(TOP_FOLDERS + 1);
    const other = step1.at(-1)!;
    expect(other.other).toBe(true);
    expect(other.label).toBe("3 more folders");
    expect(other.folders).toEqual(["p/f5", "p/f6", "p/f7"]);
    expect(other.files).toBe(3 + 2 + 1);
  });

  it("normalizes each band to fit its block at both ends", () => {
    const reach = readReach(
      result([
        ...Array.from({ length: 6 }, (_, i) => ({ path: `packages/s/src/a/f${i}.py`, depth: 1, via: CORE })),
        ...Array.from({ length: 2 }, (_, i) => ({ path: `packages/s/src/b/f${i}.py`, depth: 1, via: CORE })),
      ]),
      [CORE],
    );
    const map = buildStepMap(reach);
    const changed = map.blocks.find((b) => b.column === 0)!;
    const out = map.bands.filter((b) => b.from === changed.key);
    expect(out).toHaveLength(2);
    expect(out.reduce((n, b) => n + b.h0, 0)).toBeCloseTo(changed.height);
    // Bands stack top to bottom with no gap or overlap.
    expect(out[0]!.y0).toBeCloseTo(changed.y);
    expect(out[1]!.y0).toBeCloseTo(changed.y + out[0]!.h0);
    for (const band of out) {
      const to = map.blocks.find((b) => b.key === band.to)!;
      expect(band.h1).toBeCloseTo(to.height);
      expect(band.y1).toBeCloseTo(to.y);
      expect(band.fromChanged).toBe(true);
    }
  });

  it("keeps blocks apart so labels never overlap", () => {
    const transitive = Array.from({ length: 300 }, (_, i) => ({
      path: i < 296 ? `packages/s/src/big/f${i}.py` : `packages/s/src/small${i}/f.py`,
      depth: 1,
      via: CORE,
    }));
    const map = buildStepMap(readReach(result(transitive), [CORE]));
    const step1 = map.blocks.filter((b) => b.column === 1);
    for (const b of step1) expect(b.height).toBeGreaterThanOrEqual(STEP_LAYOUT.minBlock);
    for (let i = 1; i < step1.length; i++)
      expect(step1[i]!.y - (step1[i - 1]!.y + step1[i - 1]!.height)).toBeGreaterThanOrEqual(STEP_LAYOUT.gap);
    expect(map.height).toBeGreaterThanOrEqual(step1.at(-1)!.y + step1.at(-1)!.height);
  });

  it("folds files deeper than three steps into the last column", () => {
    const reach = readReach(
      result([
        { path: "packages/s/src/a/f.py", depth: 1, via: CORE },
        { path: "packages/s/src/b/f.py", depth: 2, via: "packages/s/src/a/f.py" },
        { path: "packages/s/src/c/f.py", depth: 3, via: "packages/s/src/b/f.py" },
        { path: "packages/s/src/d/f.py", depth: 4, via: "packages/s/src/c/f.py" },
      ]),
      [CORE],
    );
    const map = buildStepMap(reach);
    expect(map.blocks.filter((b) => b.column === 3).map((b) => b.label)).toEqual(["s/c", "s/d"]);
    expect(map.columns[3]!.title).toBe("3+ steps");
    expect(stepWords(2)).toBe("2 steps");
  });

  it("lays out the same way every time", () => {
    const reach = readReach(
      result([
        { path: "packages/s/src/b/f.py", depth: 1, via: CORE },
        { path: "packages/s/src/a/f.py", depth: 1, via: CORE },
      ]),
      [CORE],
    );
    expect(buildStepMap(reach)).toEqual(buildStepMap(reach));
    // Equal counts order by name.
    expect(buildStepMap(reach).blocks.filter((b) => b.column === 1).map((b) => b.label)).toEqual(["s/a", "s/b"]);
  });
});

describe("defaultStepBlock", () => {
  it("opens on the step-1 block with the most poor-health files", () => {
    const reach = readReach(
      result(
        [
          ...Array.from({ length: 5 }, (_, i) => ({ path: `packages/s/src/big/f${i}.py`, depth: 1, via: CORE })),
          { path: "packages/s/src/weak/a.py", depth: 1, via: CORE },
          { path: "packages/s/src/weak/b.py", depth: 1, via: CORE },
          { path: "packages/s/src/far/a.py", depth: 2, via: "packages/s/src/weak/a.py" },
        ],
        [sig("packages/s/src/weak/a.py", 2), sig("packages/s/src/weak/b.py", 4.9), sig("packages/s/src/far/a.py", 1)],
      ),
      [CORE],
    );
    expect(defaultStepBlock(buildStepMap(reach))?.key).toBe("1:s/weak");
  });

  it("falls back to the largest step-1 block when health is fine", () => {
    const reach = readReach(
      result([
        { path: "packages/s/src/a/1.py", depth: 1, via: CORE },
        { path: "packages/s/src/b/1.py", depth: 1, via: CORE },
        { path: "packages/s/src/b/2.py", depth: 1, via: CORE },
      ]),
      [CORE],
    );
    expect(defaultStepBlock(buildStepMap(reach))?.key).toBe("1:s/b");
  });
});

describe("blockFocusPaths", () => {
  it("narrows to a reached block's files, and to what a changed folder reaches directly", () => {
    const reach = readReach(
      result([
        { path: "packages/s/src/a/1.py", depth: 1, via: CORE },
        { path: "packages/s/src/b/1.py", depth: 2, via: "packages/s/src/a/1.py" },
      ]),
      [CORE],
    );
    const map = buildStepMap(reach);
    const changed = map.blocks.find((b) => b.column === 0)!;
    const b = map.blocks.find((x) => x.key === "2:s/b")!;
    expect([...blockFocusPaths(changed, reach)]).toEqual(["packages/s/src/a/1.py"]);
    expect([...blockFocusPaths(b, reach)]).toEqual(["packages/s/src/b/1.py"]);
  });
});

describe("interaction model", () => {
  const chain = () =>
    readReach(
      result([
        { path: "packages/s/src/a/1.py", depth: 1, via: CORE },
        { path: "packages/s/src/b/1.py", depth: 1, via: CORE },
        { path: "packages/s/src/c/1.py", depth: 2, via: "packages/s/src/a/1.py" },
        { path: "packages/s/src/d/1.py", depth: 3, via: "packages/s/src/c/1.py" },
      ]),
      [CORE],
    );

  it("lights a block's whole path, upstream to the change and downstream to the last step", async () => {
    const { pathThrough } = await import("./blast-step-model");
    const map = buildStepMap(chain());
    const path = pathThrough(map, "2:s/c");
    expect([...path.blocks].sort()).toEqual(["0:core/persistence", "1:s/a", "2:s/c", "3:s/d"]);
    expect(path.blocks.has("1:s/b")).toBe(false);
    expect(path.bands.size).toBe(3);
  });

  it("keeps the files each band pulls in, and finds a file's block", async () => {
    const { blockOfPath } = await import("./blast-step-model");
    const map = buildStepMap(chain());
    expect(map.bands.find((b) => b.key === "1:s/a>2:s/c")?.paths).toEqual(["packages/s/src/c/1.py"]);
    expect(blockOfPath(map, "packages/s/src/d/1.py")?.key).toBe("3:s/d");
  });

  it("moves with arrow keys within a step and to the nearest block across steps", async () => {
    const { neighborBlock } = await import("./blast-step-model");
    const map = buildStepMap(chain());
    expect(neighborBlock(map, "1:s/a", "down")?.key).toBe("1:s/b");
    expect(neighborBlock(map, "1:s/b", "down")).toBeUndefined();
    expect(neighborBlock(map, "1:s/a", "right")?.key).toBe("2:s/c");
    expect(neighborBlock(map, "1:s/b", "left")?.key).toBe("0:core/persistence");
  });

  it("opens a folded step in place, with a control to fold it again", () => {
    const transitive = Array.from({ length: TOP_FOLDERS + 2 }, (_, i) => ({ path: `packages/p/src/f${i}/m.py`, depth: 1, via: CORE }));
    const reach = readReach(result(transitive), [CORE]);
    const folded = buildStepMap(reach).blocks.filter((b) => b.column === 1);
    expect(folded.at(-1)?.other).toBe(true);
    const open = buildStepMap(reach, new Set([1]));
    const step1 = open.blocks.filter((b) => b.column === 1);
    expect(step1.filter((b) => !b.less)).toHaveLength(TOP_FOLDERS + 2);
    expect(step1.at(-1)?.less).toBe(true);
    expect(open.columns[1]?.expanded).toBe(true);
    expect(defaultStepBlock(open)?.less).toBe(false);
  });

  it("names a block by three files, poor health first", () => {
    const reach = readReach(
      result(
        ["z", "b", "a", "c"].map((n) => ({ path: `packages/s/src/x/${n}.py`, depth: 1, via: CORE })),
        [sig("packages/s/src/x/z.py", 2)],
      ),
      [CORE],
    );
    expect(buildStepMap(reach).blocks.find((b) => b.key === "1:s/x")?.top).toEqual(["z.py", "a.py", "b.py"]);
  });
});
