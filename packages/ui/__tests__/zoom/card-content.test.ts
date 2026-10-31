import { describe, expect, it } from "vitest";

import {
  cardStatPrefixes,
  cardStats,
  cardTechnologies,
  cardTier,
  LABEL_ASPECT_MAX,
  labelAspect,
  primaryMetric,
  summaryLineCap,
  titleFontPx,
  titleMode,
  twoLineChars,
} from "../../src/zoom/card-content";
import { CARD_ASPECT } from "../../src/zoom/constants";
import type { ZoomNode } from "../../src/zoom/types";

function node(over: Partial<ZoomNode> = {}): ZoomNode {
  return {
    id: "n",
    parent_id: null,
    level: 1,
    kind: "layer",
    name: "Core",
    path: "",
    children: [],
    importance: 0.5,
    sibling_rank: 1,
    metrics: {
      file_count: 12,
      descendant_count: 12,
      hotspot_count: 0,
      dead_count: 0,
      entry_point_count: 0,
      on_flow_count: 0,
    },
    summary: "",
    language: null,
    page_id: "",
    health_score: null,
    is_entry_point: false,
    is_hotspot: false,
    is_dead: false,
    is_test: false,
    on_flow: false,
    ...over,
  };
}

describe("cardTier", () => {
  it("discloses more as the card grows", () => {
    expect(cardTier(30, 20)).toBe("hidden");
    expect(cardTier(80, 40)).toBe("tiny");
    expect(cardTier(110, 60)).toBe("small");
    // A root card on a large repo (about 130 x 87 px) shows its summary.
    expect(cardTier(130, 87)).toBe("medium");
    expect(cardTier(320, 210)).toBe("large");
  });

  it("caps summary lines by tier", () => {
    expect(summaryLineCap("small")).toBe(0);
    expect(summaryLineCap("medium")).toBe(2);
    expect(summaryLineCap("large")).toBeGreaterThan(2);
  });

  it("rounds the title font to whole px within the clamp", () => {
    expect(titleFontPx(40)).toBe(11);
    expect(titleFontPx(87)).toBe(12);
    expect(titleFontPx(1000)).toBe(17);
  });
});

describe("labelAspect", () => {
  it("balances a name over two lines, never below its longest word", () => {
    expect(twoLineChars("Command Line Interface", "words")).toBe(12);
    expect(twoLineChars("Documentation", "words")).toBe(13);
    expect(twoLineChars("zoom-detail-panel.tsx", "path")).toBe(12);
  });

  it("leaves short names at the base card aspect", () => {
    expect(labelAspect("Core", "layer")).toBeLessThan(CARD_ASPECT);
  });

  it("sizes a file's name for the floor font it is read at when its folder is framed", () => {
    expect(labelAspect("refactoring_health.py", "file")).toBeGreaterThan(
      labelAspect("refactoring_health.py", "folder"),
    );
    expect(labelAspect("ci.py", "file")).toBeLessThan(CARD_ASPECT);
  });

  it("widens cards whose name needs it, up to a cap", () => {
    expect(labelAspect("Documentation", "layer")).toBeGreaterThan(CARD_ASPECT);
    expect(labelAspect("x".repeat(80), "file")).toBe(LABEL_ASPECT_MAX);
  });
});

describe("card slots", () => {
  it("shows a file's language as its technology chip", () => {
    expect(cardTechnologies(node({ kind: "file", language: "python" }))).toEqual(["Python"]);
    expect(cardTechnologies(node())).toEqual([]);
  });

  it("moves the file language out of the footer once a chip shows it", () => {
    const f = node({ kind: "file", language: "python" });
    expect(primaryMetric(f, false)).toBe("Python");
    expect(primaryMetric(f, true)).toBe("");
    expect(primaryMetric(node(), true)).toBe("12 files");
  });

  it("summarises role counts for the stats row", () => {
    const n = node({
      metrics: { ...node().metrics, hotspot_count: 3, entry_point_count: 1 },
    });
    expect(cardStats(n)).toBe("3 hotspots · 1 entry point");
    expect(cardStats(node())).toBe("");
  });
});

describe("titleMode", () => {
  it("wraps files and path-like folders as paths, titled folders as words", () => {
    expect(titleMode("file", "Read Me.md")).toBe("path");
    expect(titleMode("folder", "user_routes")).toBe("path");
    expect(titleMode("folder", "Routers Overview")).toBe("words");
    expect(titleMode("layer", "core")).toBe("words");
  });
});

describe("cardStatPrefixes", () => {
  it("grows one whole stat at a time, ending in the full row", () => {
    const n = node({
      metrics: { ...node().metrics, hotspot_count: 3, entry_point_count: 4, dead_count: 1 },
    });
    expect(cardStatPrefixes(n)).toEqual([
      "3 hotspots",
      "3 hotspots · 4 entry points",
      "3 hotspots · 4 entry points · 1 dead file",
    ]);
    expect(cardStats(n)).toBe("3 hotspots · 4 entry points · 1 dead file");
    expect(cardStatPrefixes(node())).toEqual([]);
  });
});
