import { describe, expect, it } from "vitest";
import type { KnowledgeMatrix } from "@repowise-dev/types/owners";
import {
  DEFAULT_OFFBOARDING_THRESHOLD,
  formatLeaveParam,
  formatThresholdParam,
  parseLeaveParam,
  parseThresholdParam,
  simulateOffboarding,
} from "../../src/owners/offboarding";
import { knowledgeRisks } from "../../src/owners/knowledge-risks";
import { isSharedKnowledge, knowledgeCoverage } from "../../src/owners/knowledge-coverage";
import { quietOwnerExposure } from "../../src/owners/knowledge-loss";
import { isLowHealth, isSingleOwner } from "../../src/owners/knowledge-files";
import {
  buildHandoverPrompt,
  offboardingPromptAreas,
  riskPromptAreas,
} from "../../src/owners/handover-prompt";

const author = (key: string, active = true) => ({
  key,
  name: key.toUpperCase(),
  email: `${key}@x.dev`,
  last_commit_at: null,
  active,
});

// Authors: 0 ana, 1 bo, 2 cy. Health 9.0 is good, 3.0 is in a weak band.
const MATRIX: KnowledgeMatrix = {
  authors: [author("ana"), author("bo"), author("cy", false)],
  dirs: ["", "src/api", "src/ui"],
  files: [
    // ana alone, hotspot -> severe when ana leaves
    [1, "routes.py", 100, 9.0, true, 1, 0, [[0, 1.0]], true],
    // ana 60 / bo 40: at risk at 50% (remaining 0.4), safe at 40%
    [1, "auth.py", 50, 9.0, false, 2, 0, [[0, 0.6], [1, 0.4]], false],
    // bo 70 / ana 30: ana leaving keeps 0.7, not at risk
    [2, "view.tsx", 80, 3.0, false, 2, 1, [[1, 0.7], [0, 0.3]], true],
    // cy alone, weak health -> severe when cy leaves
    [0, "setup.py", 20, 3.0, false, 1, 2, [[2, 1.0]], true],
    // no history: bus factor 0 is never single-owner
    [0, "empty.py", 5, null, false, 0, null, [], false],
  ],
};

describe("simulateOffboarding", () => {
  it("flags files whose remaining share drops below the threshold", () => {
    const r = simulateOffboarding(MATRIX, ["ana"]);
    expect(r.threshold).toBe(DEFAULT_OFFBOARDING_THRESHOLD);
    expect(r.areas.flatMap((a) => a.files.map((f) => f.path))).toEqual(["src/api/routes.py", "src/api/auth.py"]);
    expect(r.totals.touchedFiles).toBe(3);
    expect(r.totals.atRiskFiles).toBe(2);
    expect(r.totals.severeFiles).toBe(1);
    expect(r.totals.hotspots).toBe(1);
    expect(r.totals.atRiskNloc).toBe(150);
    expect(r.totals.totalNloc).toBe(255);
    expect(r.totals.nlocShare).toBeCloseTo(150 / 255);
    expect(r.leaving.map((p) => p.key)).toEqual(["ana"]);
  });

  it("grades severity and names the strongest author who stays", () => {
    const [routes, auth] = simulateOffboarding(MATRIX, ["ana"]).areas[0]!.files;
    expect(routes?.severity).toBe("severe");
    expect(routes?.inheritor).toBeNull();
    expect(auth?.severity).toBe("at_risk");
    expect(auth?.lostShare).toBeCloseTo(0.6);
    expect(auth?.remainingShare).toBeCloseTo(0.4);
    expect(auth?.inheritor).toMatchObject({ key: "bo", share: 0.4 });
  });

  it("rolls files up by directory with one suggested inheritor", () => {
    const { areas } = simulateOffboarding(MATRIX, ["ana"]);
    expect(areas).toHaveLength(1);
    expect(areas[0]).toMatchObject({ dir: "src/api", fileCount: 2, severe: 1, hotspots: 1, nloc: 150 });
    expect(areas[0]?.inheritor).toMatchObject({ person: { key: "bo" }, files: 1 });
  });

  it("moves with the threshold", () => {
    expect(simulateOffboarding(MATRIX, ["ana"], 0.4).totals.atRiskFiles).toBe(1);
    expect(simulateOffboarding(MATRIX, ["ana"], 0.75).totals.atRiskFiles).toBe(3);
  });

  it("adds up several leavers and sorts areas severe first", () => {
    const r = simulateOffboarding(MATRIX, ["ana", "bo"]);
    expect(r.totals.atRiskFiles).toBe(3);
    expect(r.areas.map((a) => a.dir)).toEqual(["src/api", "src/ui"]);
  });

  it("counts only changing files when asked, denominators included", () => {
    const r = simulateOffboarding(MATRIX, ["ana"], 0.5, { changedOnly: true });
    expect(r.areas.flatMap((a) => a.files.map((f) => f.path))).toEqual(["src/api/routes.py"]);
    expect(r.totals.touchedFiles).toBe(2);
    expect(r.totals.totalNloc).toBe(200);
    expect(r.totals.trackedFiles).toBe(3);
    expect(r.totals.nlocShare).toBeCloseTo(100 / 200);
  });

  it("ignores unknown keys and an empty selection", () => {
    const r = simulateOffboarding(MATRIX, ["nobody"]);
    expect(r.leaving).toEqual([]);
    expect(r.areas).toEqual([]);
    expect(r.totals.totalNloc).toBe(255);
  });
});

describe("knowledgeRisks", () => {
  it("keeps single-owner files that are hotspots or weak, grouped by directory", () => {
    const r = knowledgeRisks(MATRIX);
    expect(r.files).toBe(2);
    expect(r.hotspots).toBe(1);
    expect(r.lowHealth).toBe(1);
    expect(r.areas.map((a) => a.dir)).toEqual(["src/api", ""]);
    expect(r.areas[0]?.holder).toMatchObject({ person: { key: "ana" }, files: 1 });
    expect(r.areas[1]?.files[0]).toMatchObject({ path: "setup.py", lowHealth: true, second: null });
  });
});

describe("knowledgeCoverage", () => {
  it("weighs changing code with history by lines, shared when two or more hold it", () => {
    const c = knowledgeCoverage(MATRIX);
    // routes (100, one holder), view (80, shared), setup (20, one holder); auth and empty did not change.
    expect(c).toMatchObject({ files: 3, sharedFiles: 1, nloc: 200, sharedNloc: 80, basis: "nloc" });
    expect(c.share).toBeCloseTo(0.4);
    expect(c.singleHolderFiles).toBe(2);
    expect(c.singleHolderAreas.map((a) => a.dir)).toEqual(["src/api", ""]);
  });

  it("falls back to file counts without sizes and is null when nothing changed", () => {
    const sizeless = { ...MATRIX, files: MATRIX.files.map((f) => [f[0], f[1], 0, ...f.slice(3)] as typeof f) };
    expect(knowledgeCoverage(sizeless)).toMatchObject({ basis: "files", share: 1 / 3 });
    const still = { ...MATRIX, files: MATRIX.files.map((f) => [...f.slice(0, 8), false] as unknown as typeof f) };
    expect(knowledgeCoverage(still).share).toBeNull();
  });

  it("never counts a file without history as shared", () => {
    expect(isSharedKnowledge(0)).toBe(false);
    expect(isSharedKnowledge(1)).toBe(false);
    expect(isSharedKnowledge(2)).toBe(true);
  });
});

describe("quietOwnerExposure", () => {
  it("reads each quiet owner as a single-person off-boarding", () => {
    const rows = quietOwnerExposure(MATRIX, [
      { key: "cy", name: "CY", last_commit_at: null, files_owned: 1, changing_files_owned: 1 },
    ]);
    expect(rows[0]?.result.totals).toMatchObject({ atRiskFiles: 1, severeFiles: 1 });
  });
});

describe("predicates", () => {
  it("never counts bus factor 0 as single-owner", () => {
    expect(isSingleOwner(1)).toBe(true);
    expect(isSingleOwner(0)).toBe(false);
    expect(isSingleOwner(2)).toBe(false);
  });
  it("reads weak health from the bands and treats unscored as not weak", () => {
    expect(isLowHealth(3.0)).toBe(true);
    expect(isLowHealth(9.0)).toBe(false);
    expect(isLowHealth(null)).toBe(false);
  });
});

describe("URL params", () => {
  it("round-trips keys holding commas and percent signs", () => {
    const keys = ["a@x.dev", "name:Doe, Jane", "50%off"];
    expect(parseLeaveParam(formatLeaveParam(keys))).toEqual(keys);
    expect(parseLeaveParam("a,,a,b")).toEqual(["a", "b"]);
    expect(parseLeaveParam(null)).toEqual([]);
  });
  it("clamps whole percents to the offered range and falls back otherwise", () => {
    expect(parseThresholdParam("30")).toBe(0.3);
    expect(parseThresholdParam("0")).toBe(0.1);
    expect(parseThresholdParam("100")).toBe(0.9);
    expect(parseThresholdParam("12.5")).toBe(DEFAULT_OFFBOARDING_THRESHOLD);
    expect(parseThresholdParam("abc")).toBe(DEFAULT_OFFBOARDING_THRESHOLD);
    expect(parseThresholdParam(null)).toBe(DEFAULT_OFFBOARDING_THRESHOLD);
    expect(formatThresholdParam(0.3)).toBe("30");
  });
});

describe("buildHandoverPrompt", () => {
  it("lists areas, files and the suggested person", () => {
    const prompt = buildHandoverPrompt({
      purpose: "handover",
      people: ["ANA"],
      areas: offboardingPromptAreas(simulateOffboarding(MATRIX, ["ana"]).areas),
    });
    expect(prompt).toContain("\n\n## Knowledge handover from ANA");
    expect(prompt).toContain("`src/api/routes.py` (100% of commits, hotspot)");
    expect(prompt).toContain("Could take over: BO");
    expect(prompt).toContain("\n\n## Hard constraints\n");
    expect(prompt).toContain("less than 50% of the commits");
  });
  it("states the scenario's threshold", () => {
    const prompt = buildHandoverPrompt({ purpose: "handover", people: ["ANA"], areas: [], threshold: 0.7 });
    expect(prompt).toContain("less than 70% of the commits");
  });
  it("frames a spread plan around the holder", () => {
    const prompt = buildHandoverPrompt({
      purpose: "spread",
      people: ["CY"],
      areas: riskPromptAreas(knowledgeRisks(MATRIX).areas.filter((a) => a.dir === "")),
      flavor: "claude-code-mcp",
    });
    expect(prompt).toContain("## Spread knowledge held only by CY");
    expect(prompt).toContain("`(repository root)`");
    expect(prompt).toContain("get_risk");
  });
});
