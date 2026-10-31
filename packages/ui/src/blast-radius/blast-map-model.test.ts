import { describe, expect, it } from "vitest";
import type { BlastRadiusResponse, TransitiveEntry } from "@repowise-dev/types/blast-radius";
import { MAX_FILE_NODES, buildBlastMap, isTestFile, moduleOf, readReach, riskySpots } from "./blast-map-model";
import { reviewStatus, roughCount } from "./review-status";

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

describe("readReach", () => {
  it("counts tests apart from the code a change reaches", () => {
    const reach = readReach(
      result(
        [
          { path: "src/api/a.py", depth: 1, via: "src/core.py" },
          { path: "tests/test_core.py", depth: 1, via: "src/core.py" },
          { path: "src/web/x.py", depth: 2, via: "src/api/a.py" },
          { path: "packages/ui/a.test.tsx", depth: 2, via: "src/api/a.py" },
          { path: "lib/odd.py", depth: 3, via: "src/web/x.py" },
        ],
        {
          affected_signals: [
            // The index's word counts even where the name says nothing.
            { path: "lib/odd.py", is_hotspot: null, health_score: null, module: null, is_test: true },
            { path: "src/web/x.py", is_hotspot: true, health_score: 3.0, module: "src/web", is_test: false },
          ],
        },
      ),
      ["src/core.py"],
    );
    expect(reach.files.map((f) => f.path)).toEqual(["src/api/a.py", "src/web/x.py"]);
    expect(reach.tests).toEqual(["tests/test_core.py", "packages/ui/a.test.tsx", "lib/odd.py"]);
    expect(reach.direct).toBe(1);
    expect(reach.modules).toBe(2);
    expect(reach.maxHop).toBe(2);
  });

  it("keeps unknown signals unknown", () => {
    const reach = readReach(result([{ path: "a/b.py", depth: 1 }]), ["c.py"]);
    expect(reach.files[0]!.healthScore).toBeNull();
    expect(reach.files[0]!.isHotspot).toBeNull();
  });

  it("names a module from the index, else from the folder", () => {
    expect(moduleOf("packages/ui/src/a.ts", { path: "", is_hotspot: null, health_score: null, module: "packages/ui" })).toBe(
      "packages/ui",
    );
    expect(moduleOf("src/web/a.ts", undefined)).toBe("src/web");
    expect(moduleOf("README.md", undefined)).toBe("(root)");
  });

  it("knows a test by its name when an older server sends no flag", () => {
    for (const p of ["tests/unit/x.py", "src/a_test.go", "src/x.spec.ts", "pkg/__tests__/a.tsx", "test_a.py"])
      expect(isTestFile(p, undefined)).toBe(true);
    for (const p of ["src/testing.py", "src/attest.ts", "contest/a.py"]) expect(isTestFile(p, undefined)).toBe(false);
  });
});

describe("buildBlastMap", () => {
  it("draws a small reach file by file, each hanging from what it imports", () => {
    const map = buildBlastMap(
      readReach(
        result([
          { path: "src/api/a.py", depth: 1, via: "src/core.py" },
          { path: "src/web/x.py", depth: 2, via: "src/api/a.py" },
        ]),
        ["src/core.py"],
      ),
    );
    expect(map.mode).toBe("files");
    expect(map.files.map((n) => n.path)).toEqual(["src/api/a.py", "src/web/x.py"]);
    expect(map.files[0]!.parentKey).toBe(map.changed[0]!.key);
    expect(map.files[1]!.parentKey).toBe(map.files[0]!.key);
  });

  it("draws a large reach by module, marking modules with untested files that change often", () => {
    const transitive = Array.from({ length: MAX_FILE_NODES + 4 }, (_, i) => ({
      path: `${i < 10 ? "src/api" : "src/web"}/f${i}.py`,
      depth: i < 10 ? 1 : 2,
      via: i < 10 ? "src/core.py" : "src/api/f0.py",
    }));
    const map = buildBlastMap(
      readReach(
        result(transitive, {
          test_gaps: ["src/web/f12.py"],
          affected_signals: [{ path: "src/web/f12.py", is_hotspot: true, health_score: 9, module: "src/web" }],
        }),
        ["src/core.py"],
      ),
    );
    expect(map.mode).toBe("modules");
    expect(map.files).toEqual([]);
    const api = map.modules.find((m) => m.name === "src/api")!;
    const web = map.modules.find((m) => m.name === "src/web")!;
    expect([api.files, api.hop, api.flagged]).toEqual([10, 1, 0]);
    expect([web.files, web.hop, web.flagged]).toEqual([9, 2, 1]);
    // src/web is reached through src/api, so it hangs from it.
    expect(web.parentKey).toBe(api.key);
    expect(api.parentKey).toBeNull();
  });
});

describe("riskySpots", () => {
  it("lists changed files before their importers, and only marked ones", () => {
    const reach = readReach(
      result(
        [
          { path: "hot.py", depth: 1, via: "c.py" },
          { path: "calm.py", depth: 1, via: "c.py" },
        ],
        {
          test_gaps: ["c.py"],
          affected_signals: [{ path: "hot.py", is_hotspot: true, health_score: 8.0, module: null }],
        },
      ),
      ["c.py"],
    );
    expect(riskySpots(reach).map((n) => [n.path, n.changed])).toEqual([
      ["c.py", true],
      ["hot.py", false],
    ]);
  });
});

describe("reviewStatus", () => {
  const facts = (over: Partial<Parameters<typeof reviewStatus>[0]> = {}) => ({
    reach: readReach(result([]), ["src/a.py"]),
    total: 1,
    testsTotal: 2,
    missingPartners: 0,
    band: "localized" as const,
    ...over,
  });

  it("calls a small, calm change small, with its size", () => {
    expect(reviewStatus(facts())).toEqual({ tone: "calm", word: "Small change", reason: "1 file changed, 2 tests to run." });
  });

  it("asks for tests first when a changed file has none", () => {
    const reach = readReach(result([], { test_gaps: ["src/a.py"] }), ["src/a.py"]);
    expect(reviewStatus(facts({ reach }))).toMatchObject({ word: "Needs tests", reason: "1 changed file with no test found." });
  });

  it("says look first for a changed hotspot, a wide reach, or a left-out partner", () => {
    const hot = readReach(
      result([], { affected_signals: [{ path: "src/a.py", is_hotspot: true, health_score: 9, module: null }] }),
      ["src/a.py"],
    );
    expect(reviewStatus(facts({ reach: hot })).reason).toBe("1 changed file changes often or has weak health.");
    const wide = readReach(
      result(Array.from({ length: 2400 }, (_, i) => ({ path: `src/m/f${i}.py`, depth: 2 }))),
      ["src/a.py"],
    );
    expect(reviewStatus(facts({ reach: wide })).reason).toBe("2,000+ source files depend on it.");
    expect(reviewStatus(facts({ missingPartners: 2 })).reason).toBe("2 files that usually change with these are not in it.");
  });

  it("rounds big figures down to what was measured", () => {
    expect(roughCount(999)).toBe("999");
    expect(roughCount(2295)).toBe("2,000+");
  });
});
