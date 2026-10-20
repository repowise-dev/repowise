import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type {
  CouplingAreaPair,
  CouplingAreasResponse,
  CouplingEdge,
} from "@repowise-dev/types/coupling";
import { CouplingAreaPairList } from "../../src/coupling/area-pair-list.js";
import { DEFAULT_AREA_COUPLING_COPY } from "../../src/coupling/area-copy.js";
import {
  DEFAULT_AREA_FILTERS,
  parseAreaPair,
  serializeAreaPair,
  toCouplingFilters,
} from "../../src/coupling/area-view-state.js";
import { couplingIntensityByArea, couplingIntensityByFile } from "../../src/coupling/lens.js";
import { buildAreaCouplingAiPrompt } from "../../src/health/ai-prompt-builder.js";

function areaPair(
  source: string,
  target: string,
  unexplained: number,
  pairs = 10,
  linked = false,
): CouplingAreaPair {
  return {
    linked,
    hidden: !linked && unexplained > 0,
    source,
    target,
    pairs,
    unexplained_pairs: unexplained,
    unexplained_share: unexplained / pairs,
    strength: 2,
    co_changes: 30,
    last_co_change: "2026-10-15",
    top_pairs: [],
  };
}

function response(pairs: CouplingAreaPair[]): CouplingAreasResponse {
  return {
    root: "",
    depth: 2,
    totals: {
      pairs: 20,
      cross_area_pairs: 20,
      unexplained_pairs: pairs.reduce((n, p) => n + p.unexplained_pairs, 0),
      area_pairs: pairs.length + 3,
      unexplained_area_pairs: pairs.filter((p) => p.unexplained_pairs > 0).length,
      hidden_area_pairs: pairs.filter((p) => p.hidden).length,
      unlabelled_pairs: 0,
    },
    areas: [],
    area_pairs: pairs,
  };
}

function renderList(data: CouplingAreasResponse) {
  const onOpen = vi.fn();
  const onPrompt = vi.fn();
  render(
    <CouplingAreaPairList
      data={data}
      copy={DEFAULT_AREA_COUPLING_COPY}
      testsIncluded={false}
      onOpen={onOpen}
      onPrompt={onPrompt}
    />,
  );
  return { onOpen, onPrompt };
}

describe("CouplingAreaPairList", () => {
  it("marks only area pairs with no dependency between them", () => {
    const pairs = [
      areaPair("packages/server", "packages/ui", 7),
      areaPair("packages/ui", "packages/web", 9, 10, true),
      areaPair("docs", "packages/core", 0),
    ];
    const { onOpen, onPrompt } = renderList(response(pairs));
    expect(screen.getByText(/This area pair changes together although no file in one imports/)).toBeTruthy();
    expect(screen.getByText(/Test files are left out/)).toBeTruthy();
    // One mark, on the hidden pair; the layered pair reads as plain figures.
    expect(screen.getAllByText("No import link between the areas")).toHaveLength(1);
    expect(screen.getByText("9 file pairs with no direct import (90%)")).toBeTruthy();
    expect(screen.getByText("All explained")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /^Copy prompt for your agent/ })).toHaveLength(1);
    expect(screen.getByText(/Showing the top 3 of 6 area pairs/)).toBeTruthy();

    fireEvent.click(screen.getByText("packages/server/ ↔ packages/ui/"));
    expect(onOpen).toHaveBeenCalledWith({ source: "packages/server", target: "packages/ui" });
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt for these 1" }));
    expect(onPrompt).toHaveBeenCalledWith([pairs[0]]);
  });

  it("names a clean result with its scope", () => {
    renderList(response([areaPair("packages/ui", "packages/web", 4, 10, true)]));
    expect(
      screen.getByText("Every area pair that changes together also has an import link, 2 levels down"),
    ).toBeTruthy();
    expect(screen.getByText(/Checked 20 file pairs/)).toBeTruthy();
  });

  it("gives no verdict on an index that predates the dependency check", () => {
    const data = response([areaPair("a", "b", 0)]);
    data.totals.unlabelled_pairs = data.totals.pairs;
    renderList(data);
    expect(screen.getByText(/predates the dependency check/)).toBeTruthy();
    expect(screen.queryByText(/Every area pair/)).toBeNull();
  });
});

describe("area view state", () => {
  it("round-trips an area pair, the root included", () => {
    const pair = { source: "", target: "packages" };
    expect(parseAreaPair(serializeAreaPair(pair))).toEqual(pair);
    expect(parseAreaPair("all")).toBeNull();
    expect(parseAreaPair("a|b|c")).toBeNull();
  });

  it("leaves tests out and omits unset filters by default", () => {
    expect(toCouplingFilters(DEFAULT_AREA_FILTERS)).toEqual({
      root: "",
      unexplained: false,
      crossArea: true,
      excludeTests: true,
    });
  });
});

describe("coupling lens", () => {
  it("normalises unexplained pairs against the most coupled area", () => {
    const lens = couplingIntensityByArea([
      { path: "a", pairs: 10, unexplained_pairs: 4, unexplained_share: 0.4, strength: 1, co_changes: 1 },
      { path: "b", pairs: 2, unexplained_pairs: 2, unexplained_share: 1, strength: 1, co_changes: 1 },
    ]);
    expect(lens.get("a")?.intensity).toBe(1);
    expect(lens.get("b")?.intensity).toBe(0.5);
    expect(couplingIntensityByArea([], { measure: "all" }).size).toBe(0);
  });

  it("counts each pair for both files", () => {
    const e = (s: string, t: string, structural: CouplingEdge["structural"]): CouplingEdge => ({
      source: s,
      target: t,
      strength: 1,
      last_co_change: null,
      support: 2,
      confidence_ab: null,
      confidence_ba: null,
      structural,
      dependency_kind: null,
    });
    const lens = couplingIntensityByFile([e("x", "y", "unexplained"), e("x", "z", "corroborated")]);
    expect(lens.get("x")).toEqual({ intensity: 1, pairs: 2, unexplained: 1 });
    expect(lens.get("z")?.intensity).toBe(0);
  });
});

describe("buildAreaCouplingAiPrompt", () => {
  it("hands the agent each area pair and its strongest file pairs", () => {
    const prompt = buildAreaCouplingAiPrompt({
      repoName: "repowise",
      areaPairs: [
        {
          ...areaPair("packages/server", "packages/types", 5),
          top_pairs: [
            { source: "packages/server/a.py", target: "packages/types/a.ts", structural: "unexplained", support: 9 },
          ],
        },
      ],
    });
    expect(prompt).toContain("## Hidden coupling between areas (`repowise`)");
    expect(prompt).toContain("### `packages/server/` ↔ `packages/types/`");
    expect(prompt).toContain("no dependency joins the two areas in either direction; **5**");
    expect(prompt).toContain("`packages/server/a.py` ↔ `packages/types/a.ts` (no dependency joins them, 9 shared commits)");
  });
});
