/**
 * The context rail and the pure pieces behind it: which areas it points at,
 * the area lenses, the area prompt, and the card a reader acts from.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import type { AreaDetail } from "@repowise-dev/types/areas";

import { deriveAreaIndex, type AreaIndex, type MapArea } from "../../src/health/map/areas.js";
import { areasNeedingAttention, mostImproved } from "../../src/health/map/area-rank.js";
import { KNOWLEDGE_LENS, ownerLens, shareFill } from "../../src/health/map/area-lenses.js";
import { OVERLAY_SPECS, lensAvailable, resolveLens } from "../../src/health/map/lens-registry.js";
import { NEUTRAL_FILL, QUIET_FILL } from "../../src/health/map/lens.js";
import { areaFills } from "../../src/health/map/fills.js";
import { buildAreaAiPrompt } from "../../src/health/ai-prompts/area-prompt.js";
import { AreaCard } from "../../src/health/area-rail/area-card.js";
import { DEFAULT_AREA_RAIL_LABELS } from "../../src/health/area-rail/labels.js";
import { NODE_RAMP_STOP, healthBandMapFill } from "../../src/health/tokens.js";
import { formatScore } from "@repowise-dev/types/health";
import { scoreText } from "../../src/health/map/lens.js";
import { mapFile as f } from "./map-test-utils.js";

/** An index whose areas carry the server's ownership and delta fields. */
function withRollups(index: AreaIndex, patch: Record<string, Partial<MapArea>>): AreaIndex {
  const areas = new Map(index.areas);
  for (const [path, p] of Object.entries(patch)) areas.set(path, { ...areas.get(path)!, ...p });
  return { ...index, areas };
}

const files = [
  ...Array.from({ length: 6 }, (_, i) => f(`app/bad/x${i}.py`, 50, "app", { score: 3 })),
  ...Array.from({ length: 6 }, (_, i) => f(`app/bad/inner/y${i}.py`, 50, "app", { score: 2 })),
  ...Array.from({ length: 6 }, (_, i) => f(`app/ok/z${i}.py`, 50, "app", { score: 7 })),
  ...Array.from({ length: 6 }, (_, i) => f(`lib/w${i}.py`, 50, "lib", { score: 9 })),
  f("lib/tiny/t.py", 5, "lib", { score: 1 }),
];
const index = deriveAreaIndex(files);

describe("where the rail points", () => {
  it("names the worst places, never one nested in another and never a sliver", () => {
    const picks = areasNeedingAttention(index).map((a) => a.path);
    expect(picks[0]).toBe("app/bad/inner");
    expect(picks).not.toContain("app/bad"); // contains the first pick
    expect(picks).not.toContain("lib/tiny"); // one file is a detail
  });

  it("scopes to the selected area", () => {
    expect(areasNeedingAttention(index, "lib").map((a) => a.path)).toEqual([]);
    expect(areasNeedingAttention(index, "app").map((a) => a.path)).toContain("app/ok");
  });

  it("names the area that improved most, and nothing when none did", () => {
    expect(mostImproved(index)).toBeNull();
    const up = withRollups(index, { lib: { delta30d: 0.4 }, app: { delta30d: 0.9 } });
    expect(mostImproved(up)!.path).toBe("app");
  });
});

describe("area lenses", () => {
  it("knowledge risk bands the share only one person has changed", () => {
    const a = { ...index.areas.get("lib")!, busFactor1Count: 6 };
    expect(KNOWLEDGE_LENS.areaFill!(a)).toBe(NODE_RAMP_STOP.alert);
    expect(KNOWLEDGE_LENS.areaText!(a)).toBe("86%");
    expect(shareFill(0.1, [0.25, 0.5, 0.75])).toBe(QUIET_FILL);
    expect(shareFill(null, [0.25, 0.5, 0.75])).toBe(NEUTRAL_FILL);
  });

  it("main author colours each area by its lead and keys every colour by name", () => {
    const owned = withRollups(index, {
      app: { primaryOwner: { key: "ada", name: "Ada Lovelace", share: 0.8 } },
      "app/bad": { primaryOwner: { key: "ada", name: "Ada Lovelace", share: 0.9 } },
      lib: { primaryOwner: { key: "bob", name: "Bob", share: 0.6 } },
    });
    const lens = ownerLens(owned);
    const ada = lens.areaFill!(owned.areas.get("app")!);
    expect(ada).not.toBe(lens.areaFill!(owned.areas.get("lib")!));
    expect(lens.legend.map((r) => r.label)).toContain("Ada Lovelace");
    expect(lens.areaText!(owned.areas.get("app")!)).toBe("Ada 80%");
    // An area with no owner signal at all says so, rather than borrowing a colour.
    expect(lens.areaFill!(owned.areas.get("lib/tiny")!)).toBeNull();
  });

  it("offers an area lens only where the rollup carries its signal", () => {
    expect(lensAvailable("owner", files, index)).toBe(false);
    expect(lensAvailable("knowledge", files, withRollups(index, { lib: { busFactor1Count: 1 } }))).toBe(true);
    expect(resolveLens("owner", index).id).toBe("owner");
  });

  it("colours an area by its rollup, else by its loaded files", () => {
    const fills = areaFills(index, OVERLAY_SPECS.health, files).fill;
    expect(fills.get("app/bad/inner")).toBe(healthBandMapFill("at_risk"));
    const maint = areaFills(index, OVERLAY_SPECS.maintainability, files).fill;
    expect(maint.get("lib")).toBe(NEUTRAL_FILL); // no maintainability on these rows
  });
});

const detail: AreaDetail = {
  area: {
    path: "app",
    name: "app",
    depth: 1,
    parent: "",
    has_children: true,
    direct_file_count: 0,
    file_count: 18,
    nloc: 900,
    health: 4.2,
    band: "needs_work",
    band_counts: { at_risk: 12, needs_work: 0, fair: 0, good: 6, excellent: 0 },
    worst_file: { path: "app/bad/inner/y0.py", score: 2 },
    hotspot_count: 3,
    bus_factor_1_count: 5,
    primary_owner: { key: "ada", name: "Ada Lovelace", kind: "human", files: 15, share: 0.83 },
    line_coverage_pct: null,
    coverage_file_count: 0,
    findings: { critical: 0, high: 2, medium: 0, low: 0 },
    health_delta_30d: 0.42,
  },
  distribution: {
    total_files: 18,
    total_nloc: 900,
    bands: {
      at_risk: { files: 12, nloc: 600, pct: 66.7 },
      needs_work: { files: 0, nloc: 0, pct: 0 },
      fair: { files: 0, nloc: 0, pct: 0 },
      good: { files: 6, nloc: 300, pct: 33.3 },
      excellent: { files: 0, nloc: 0, pct: 0 },
    },
  },
  worst_files: [{ path: "app/bad/inner/y0.py", score: 2, band: "at_risk", nloc: 50 }],
  hotspots: { total: 3, files: [] },
  owners: { total: 1, top: [{ key: "ada", name: "Ada Lovelace", kind: "human", files: 15, share: 0.83 }] },
  trend: [
    { taken_at: "2026-09-20T00:00:00", health: 3.8 },
    { taken_at: "2026-10-18T00:00:00", health: 4.2 },
  ],
  top_findings: [
    {
      id: "f1",
      file_path: "app/bad/inner/y0.py",
      biomarker_type: "complex_method",
      severity: "high",
      function_name: "run",
      line_start: 12,
      health_impact: 1.2,
      reason: "run branches 30 ways",
    },
    {
      id: "f2",
      file_path: "app/bad/x0.py",
      biomarker_type: "large_method",
      severity: "high",
      function_name: "load",
      line_start: 3,
      health_impact: 0.8,
      reason: "load is 400 lines",
    },
  ],
  test_reach: { files: 18, with_test_file: 4, measured_files: 0, line_coverage_pct: null },
  delta_window: { target_days: 30, latest_at: "2026-10-18T00:00:00", baseline_at: "2026-09-20T00:00:00" },
};

describe("the area prompt", () => {
  it("names the area, groups findings by file and keeps the shared closing", () => {
    const text = buildAreaAiPrompt({
      area: "app",
      score: 4.2,
      fileCount: 18,
      findings: detail.top_findings,
      worstFiles: detail.worst_files,
      flavor: "claude-code-mcp",
    });
    expect(text).toContain("`app/`");
    expect(text).toContain("### `app/bad/inner/y0.py`");
    expect(text).toContain("## Findings to work through (2)");
    expect(text).toContain("get_health");
    expect(text).toContain("## Hard constraints");
  });
});

describe("the area card", () => {
  const props = {
    detail,
    index,
    nameOf: (p: string) => p,
    labels: DEFAULT_AREA_RAIL_LABELS,
    onSelectArea: vi.fn(),
    onSelectFile: vi.fn(),
    openPrompt: vi.fn(),
  };

  it("prints the root score exactly as the page headline does", () => {
    // The headline and the overview both say 8.08. formatScore truncates so a
    // figure never reads as a better band than it is; the map and rail must
    // print the same digits, not round 8.08 up to 8.1 beside an 8.0 headline.
    const repo = { ...detail, area: { ...detail.area, path: "", health: 8.08, band: "good" as const } };
    const { getByText } = render(<AreaCard {...props} detail={repo} />);
    expect(formatScore(8.08)).toBe("8.0");
    expect(getByText("8.0")).toBeInTheDocument();
    expect(OVERLAY_SPECS.health.areaText!({ ...index.areas.get("")!, health: 8.08 })).toBe("8.0");
    expect(scoreText(f("a.py", 10, null, { score: 8.08 }))).toBe("8.0");
  });

  it("leads with the score, its band word and the good news when it improved", () => {
    const { getByText, getByTestId } = render(<AreaCard {...props} />);
    expect(getByText("4.2")).toBeInTheDocument();
    expect(getByText("Needs work")).toBeInTheDocument();
    expect(getByTestId("area-improved").textContent).toContain("+0.42");
  });

  it("makes the agent prompt for the area's findings its one filled action", () => {
    const openPrompt = vi.fn();
    const { getByRole } = render(<AreaCard {...props} openPrompt={openPrompt} />);
    fireEvent.click(getByRole("button", { name: "Copy prompt for the top 2 findings" }));
    const req = openPrompt.mock.calls[0]![0];
    expect(req.getPrompt("generic")).toContain("app/bad/x0.py");
  });

  it("opens sub-areas on the map and files in the rail", () => {
    const onSelectArea = vi.fn();
    const onSelectFile = vi.fn();
    const { getAllByRole } = render(<AreaCard {...props} onSelectArea={onSelectArea} onSelectFile={onSelectFile} />);
    fireEvent.click(getAllByRole("button", { name: /app\/bad\/inner/ })[0]!);
    expect(onSelectArea).toHaveBeenCalledWith("app/bad/inner");
    fireEvent.click(getAllByRole("button", { name: /y0\.py/ })[0]!);
    expect(onSelectFile).toHaveBeenCalledWith("app/bad/inner/y0.py");
  });

  it("names a clean area with its scope instead of offering an empty prompt", () => {
    const { getByText, queryByRole } = render(<AreaCard {...props} detail={{ ...detail, top_findings: [] }} />);
    expect(getByText("No open findings across 18 files.")).toBeInTheDocument();
    expect(queryByRole("button", { name: /Copy prompt/ })).not.toBeInTheDocument();
  });

  it("links out only where the host gave a destination", () => {
    const onNavigate = vi.fn();
    const { getByRole, queryByRole } = render(
      <AreaCard {...props} links={{ findings: "/f?area=app" }} onNavigate={onNavigate} />,
    );
    fireEvent.click(getByRole("link", { name: "Findings here" }));
    expect(onNavigate).toHaveBeenCalledWith("/f?area=app");
    expect(queryByRole("link", { name: "Contributors here" })).not.toBeInTheDocument();
  });
});
