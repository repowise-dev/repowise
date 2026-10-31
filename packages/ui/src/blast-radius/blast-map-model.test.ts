import { describe, expect, it } from "vitest";
import type { BlastRadiusResponse, TransitiveEntry } from "@repowise-dev/types/blast-radius";
import { MAX_FIRST_HOP, buildBlastMap, moduleOf, riskySpots } from "./blast-map-model";

function result(
  transitive: TransitiveEntry[],
  extra: Partial<BlastRadiusResponse> = {},
): BlastRadiusResponse {
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
    ...extra,
  };
}

describe("buildBlastMap", () => {
  it("draws first-hop files one by one and groups further hops by module", () => {
    const map = buildBlastMap(
      result(
        [
          { path: "src/api/a.py", depth: 1, via: "src/core.py" },
          { path: "src/web/x.py", depth: 2, via: "src/api/a.py" },
          { path: "src/web/y.py", depth: 2, via: "src/api/a.py" },
          { path: "lib/z.py", depth: 3, via: "src/web/x.py" },
        ],
        {
          affected_signals: [
            { path: "src/web/x.py", is_hotspot: true, health_score: 3.0, module: "src/web" },
            { path: "src/web/y.py", is_hotspot: false, health_score: 9.0, module: "src/web" },
          ],
        },
      ),
      ["src/core.py"],
    );

    expect(map.changed.map((n) => n.path)).toEqual(["src/core.py"]);
    expect(map.firstHop.map((n) => n.path)).toEqual(["src/api/a.py"]);
    // The first-hop file hangs from the changed file it imports.
    expect(map.firstHop[0]!.parentKey).toBe(map.changed[0]!.key);

    const web = map.modules.find((m) => m.name === "src/web")!;
    expect(web.files).toHaveLength(2);
    expect(web.hop).toBe(2);
    expect(web.hotspots).toBe(1);
    expect(web.weak).toBe(1);
    expect(web.parentKey).toBe(map.firstHop[0]!.key);
    expect(map.reachedFiles).toBe(4);
    expect(map.reachedModules).toBe(3);
    expect(map.maxHop).toBe(3);
  });

  it("keeps unknown signals unknown", () => {
    const map = buildBlastMap(result([{ path: "a/b.py", depth: 1 }]), ["c.py"]);
    expect(map.firstHop[0]!.healthScore).toBeNull();
    expect(map.firstHop[0]!.isHotspot).toBeNull();
    // No `via` (an older server): attached to the centre.
    expect(map.firstHop[0]!.parentKey).toBeNull();
  });

  it("caps the first ring, keeps the riskiest, and counts the rest", () => {
    const transitive = Array.from({ length: MAX_FIRST_HOP + 5 }, (_, i) => ({
      path: `f${String(i).padStart(2, "0")}.py`,
      depth: 1,
    }));
    const map = buildBlastMap(result(transitive, { test_gaps: ["f22.py"] }), ["c.py"]);
    expect(map.firstHop).toHaveLength(MAX_FIRST_HOP);
    expect(map.hiddenFirstHop).toBe(5);
    expect(map.firstHop.some((n) => n.path === "f22.py")).toBe(true);
  });

  it("names a module from the index, else from the folder", () => {
    expect(moduleOf("packages/ui/src/a.ts", { path: "", is_hotspot: null, health_score: null, module: "packages/ui" })).toBe(
      "packages/ui",
    );
    expect(moduleOf("src/web/a.ts", undefined)).toBe("src/web");
    expect(moduleOf("README.md", undefined)).toBe("(root)");
  });
});

describe("riskySpots", () => {
  it("lists changed files before their importers, and only marked ones", () => {
    const map = buildBlastMap(
      result([
        { path: "hot.py", depth: 1, via: "c.py" },
        { path: "calm.py", depth: 1, via: "c.py" },
      ], {
        test_gaps: ["c.py"],
        affected_signals: [{ path: "hot.py", is_hotspot: true, health_score: 8.0, module: null }],
      }),
      ["c.py"],
    );
    expect(riskySpots(map).map((n) => n.path)).toEqual(["c.py", "hot.py"]);
  });
});
