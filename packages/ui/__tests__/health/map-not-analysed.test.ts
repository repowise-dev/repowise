/**
 * A file a lens has no signal for is not analysed, under every lens: grey,
 * left out of every folder's fold, never read as the worst value. And a
 * folder folds by share, so size alone never paints it red.
 */
import { describe, it, expect } from "vitest";
import { deriveAreaIndex, type AreaIndex } from "../../src/health/map/areas.js";
import { layoutAreas, layoutFiles } from "../../src/health/map/area-layout.js";
import { areaFills, fileFills } from "../../src/health/map/fills.js";
import { buildScene } from "../../src/health/map/paint.js";
import { KNOWLEDGE_LENS, ownerLens } from "../../src/health/map/area-lenses.js";
import { OVERLAY_SPECS, resolveLens } from "../../src/health/map/lens-registry.js";
import { CLEAR_NEUTRAL_FILL, NEUTRAL_FILL, QUIET_FILL, type MapLens } from "../../src/health/map/lens.js";
import { NODE_RAMP_STOP } from "../../src/health/tokens.js";
import type { CodeHealthMapFile } from "../../src/health/map/types.js";
import { mapFile as f } from "./map-test-utils.js";

/** A file with every lens's signal absent. */
const bare = (path: string): CodeHealthMapFile => ({
  file_path: path,
  nloc: 40,
  score: null,
  module: null,
  line_coverage_pct: null,
  has_test_file: false,
});

function paint(lens: MapLens, files: CodeHealthMapFile[]) {
  const index = deriveAreaIndex(files);
  const scene = buildScene(layoutFiles(files, index, layoutAreas(index)));
  const { fill, analysed } = areaFills(index, lens, files);
  return { index, fill, analysed, files: fileFills(scene, lens, fill) };
}

const FILE_LENSES = ["health", "maintainability", "performance", "coverage", "churn", "dead-code", "security"] as const;

describe("not analysed is grey, under every lens", () => {
  for (const id of FILE_LENSES) {
    it(`${id}: a file without the signal, and a folder of only such files`, () => {
      const { fill, files, analysed } = paint(OVERLAY_SPECS[id], [bare("pkg/a.py"), bare("pkg/b.py")]);
      expect(files.every((x) => x === NEUTRAL_FILL)).toBe(true);
      if (!OVERLAY_SPECS[id].areaFill) expect(fill.get("pkg")).toBe(NEUTRAL_FILL);
      expect(analysed.get("pkg")).toEqual({ analysed: 0, loaded: 2 });
    });
  }

  it("main author and knowledge risk: an area with no ownership signal is not coloured", () => {
    const index: AreaIndex = deriveAreaIndex([bare("pkg/a.py")]);
    const pkg = index.areas.get("pkg")!;
    expect(ownerLens(index).areaFill!(pkg)).toBeNull();
    expect(KNOWLEDGE_LENS.areaFill!(pkg)).toBe(NEUTRAL_FILL);
    expect(areaFills(index, KNOWLEDGE_LENS, []).fill.get("pkg")).toBe(NEUTRAL_FILL);
  });
});

describe("performance folds by share over analysed files", () => {
  const analysed = (path: string, causes: number) =>
    f(path, 40, null, { performance_analyzed: true, performance_opportunities: causes });

  it("a big folder with a few causes is not painted worst", () => {
    // packages/core: 78 of 1,527 analysed files carry a cause (5%).
    const files = [
      ...Array.from({ length: 95 }, (_, i) => analysed(`core/f${i}.py`, 0)),
      ...Array.from({ length: 5 }, (_, i) => analysed(`core/c${i}.py`, 3)),
    ];
    const { fill } = paint(OVERLAY_SPECS.performance, files);
    expect(fill.get("core")).toBe(NODE_RAMP_STOP.warning);
  });

  it("leaves unanalysed files out of the share and grey", () => {
    const files = [analysed("core/c.py", 2), ...Array.from({ length: 30 }, (_, i) => bare(`core/u${i}.py`))];
    const { fill, analysed: counts } = paint(OVERLAY_SPECS.performance, files);
    // One analysed file, and it has a cause: the share is of what was analysed.
    expect(fill.get("core")).toBe(NODE_RAMP_STOP.alert);
    expect(counts.get("core")).toEqual({ analysed: 1, loaded: 31, flagged: 1 });
  });

  it("tells a checked, clear folder from an unchecked one, without claiming fast", () => {
    const { fill, analysed: counts } = paint(OVERLAY_SPECS.performance, [analysed("core/a.py", 0)]);
    expect(fill.get("core")).toBe(CLEAR_NEUTRAL_FILL);
    expect(fill.get("core")).not.toBe(NEUTRAL_FILL);
    expect(counts.get("core")).toEqual({ analysed: 1, loaded: 1, flagged: 0 });
  });

  it("dead code and security call a checked, clean folder clean", () => {
    for (const [id, row] of [
      ["dead-code", { dead_code_lines: 0 }],
      ["security", { security_findings: 0 }],
    ] as const) {
      const { fill } = paint(OVERLAY_SPECS[id], [f("lib/a.py", 40, null, row)]);
      expect(fill.get("lib")).toBe(QUIET_FILL);
    }
  });
});

describe("hotspot health", () => {
  const hot = (path: string, score: number) => f(path, 40, null, { score, is_hotspot: true });
  const calm = (path: string, score: number) => f(path, 40, null, { score, is_hotspot: false });

  it("greys every file that is not a hotspot and keeps a hotspot's band", () => {
    const lens = OVERLAY_SPECS.hotspot;
    expect(lens.fill(calm("a.py", 2))).toBe(NEUTRAL_FILL);
    expect(lens.fill(hot("b.py", 2))).toBe(OVERLAY_SPECS.health.fill(hot("b.py", 2)));
    expect(lens.fill(hot("c.py", 9.5))).toBe(OVERLAY_SPECS.health.fill(hot("c.py", 9.5)));
  });

  it("colours a folder by its hotspots' health and names their count", () => {
    const files = [hot("lib/a.py", 2), calm("lib/b.py", 9.5), calm("lib/c.py", 9.5)];
    const index = deriveAreaIndex(files);
    const { fill, text } = areaFills(index, OVERLAY_SPECS.hotspot, files);
    expect(fill.get("lib")).toBe(OVERLAY_SPECS.health.fill(hot("x.py", 2)));
    expect(text.get("lib")).toBe("1 hot · 2.0");
  });

  it("leaves a folder with no hotspot grey and unlabelled", () => {
    const files = [calm("lib/b.py", 3)];
    const { fill, text } = areaFills(deriveAreaIndex(files), OVERLAY_SPECS.hotspot, files);
    expect(fill.get("lib")).toBe(NEUTRAL_FILL);
    expect(text.get("lib")).toBeNull();
  });
});

describe("one label per visual disc", () => {
  it("drops a folder's label when one sub-folder fills it, and names the pair once", async () => {
    const { mergeDominantLabels } = await import("../../src/health/map/field-notes.js");
    const files = [
      ...Array.from({ length: 40 }, (_, i) => f(`pkg/core/src/repowise/core/m${i}.py`, 300, null)),
      f("pkg/core/alembic/v1.py", 5, null),
      f("pkg/ui/a.ts", 40, null),
    ];
    const index = deriveAreaIndex(files);
    const labels = mergeDominantLabels(index, layoutAreas(index), new Map(), "Repo");
    const core = labels.find((l) => l.path === "pkg/core")!;
    const chain = labels.find((l) => l.path === "pkg/core/src/repowise/core")!;
    expect(core.dominated).toBe(true);
    // pkg is filled by pkg/core in turn, so the one label names the whole run.
    expect(chain.name).toBe("pkg/core/src/repowise/core");
    expect(labels.find((l) => l.path === "pkg")!.dominated).toBe(true);
    expect(labels.find((l) => l.path === "pkg/ui")!.dominated).toBe(false);
  });

  it("words the hotspot chip through the host's labels", () => {
    const files = [f("lib/a.py", 40, null, { score: 2, is_hotspot: true })];
    const index = deriveAreaIndex(files);
    const lens = resolveLens("hotspot", index, { hotspotChip: (n, h) => `${n} 个热点 · ${h}` });
    expect(areaFills(index, lens, files).text.get("lib")).toBe("1 个热点 · 2.0");
  });
});
