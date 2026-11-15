import { describe, expect, it } from "vitest";

import {
  cardRoleMarks,
  cardScore,
  cardTechPrefixes,
  cardTier,
  LABEL_ASPECT_MAX,
  labelAspect,
  primaryMetric,
  setCardBadge,
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
    // A short name widens the card only by the corner score's room.
    expect(labelAspect("ci.py", "file")).toBeLessThan(CARD_ASPECT * 1.05);
  });

  it("widens cards whose name needs it, up to a cap", () => {
    expect(labelAspect("Documentation", "layer")).toBeGreaterThan(CARD_ASPECT);
    expect(labelAspect("x".repeat(80), "file")).toBe(LABEL_ASPECT_MAX);
  });
});

describe("card slots", () => {
  it("lists technologies as whole-item prefixes, and none for a file", () => {
    expect(cardTechPrefixes(node({ technologies: ["python", "typescript"] }))).toEqual([
      "Python",
      "Python · TypeScript",
    ]);
    expect(cardTechPrefixes(node({ kind: "file", language: "python" }))).toEqual([]);
    expect(cardTechPrefixes(node())).toEqual([]);
  });

  it("ends the technology line with the container badge, and refreshes when it changes", () => {
    const n = node({ technologies: ["python"] });
    expect(cardTechPrefixes(n)).toEqual(["Python"]);
    setCardBadge(n, "CLI");
    expect(cardTechPrefixes(n)).toEqual(["Python", "Python · CLI"]);
    setCardBadge(n, null);
    expect(cardTechPrefixes(n)).toEqual(["Python"]);
  });

  it("counts files on a container and shows nothing for a file (no language label)", () => {
    expect(primaryMetric(node())).toBe("12 files");
    expect(primaryMetric(node({ metrics: { ...node().metrics, file_count: 1 } }))).toBe("1 file");
    expect(primaryMetric(node({ kind: "file", language: "python" }))).toBe("");
  });

  it("formats the corner score rounded down, and nothing when unscored", () => {
    expect(cardScore(node({ health_score: 6.98 }))).toBe("6.9");
    expect(cardScore(node({ health_score: 10 }))).toBe("10.0");
    expect(cardScore(node())).toBeNull();
  });
});

describe("cardRoleMarks", () => {
  it("marks a container's non-zero counts, hotspot then entry then dead", () => {
    const n = node({
      metrics: { ...node().metrics, hotspot_count: 3, entry_point_count: 0, dead_count: 1, on_flow_count: 9 },
    });
    expect(cardRoleMarks(n)).toEqual([
      { role: "hotspot", count: "3" },
      { role: "dead", count: "1" },
    ]);
    expect(cardRoleMarks(node())).toEqual([]);
  });

  it("marks a file's own roles with no count, and never on-flow", () => {
    const f = node({ kind: "file", is_entry_point: true, on_flow: true });
    expect(cardRoleMarks(f)).toEqual([{ role: "entry", count: "" }]);
    expect(cardRoleMarks(node({ kind: "file", on_flow: true }))).toEqual([]);
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
