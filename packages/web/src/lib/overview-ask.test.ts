import { describe, expect, it } from "vitest";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";
import { overviewAskSuggestions } from "./overview-ask";

const t = (key: string, values?: Record<string, string>) =>
  values ? `${key}:${Object.values(values).join(",")}` : key;

function summary(overrides: {
  hotspot?: string;
  worst?: string | null;
  decision?: string;
  snapshots?: number;
}): OverviewSummaryResponse {
  return {
    top_hotspots: overrides.hotspot ? [{ file_path: overrides.hotspot }] : [],
    recent_decisions: overrides.decision ? [{ title: overrides.decision }] : [],
    health: {
      worst_performer_path: overrides.worst ?? null,
      history: Array.from({ length: overrides.snapshots ?? 0 }, () => ({})),
    },
  } as unknown as OverviewSummaryResponse;
}

describe("overviewAskSuggestions", () => {
  it("asks about this repo's hotspot, worst file, decision and trend, capped at four", () => {
    expect(
      overviewAskSuggestions(
        summary({ hotspot: "a.py", worst: "b.py", decision: "Use SQLite", snapshots: 2 }),
        t,
      ),
    ).toEqual(["ask.hotspotOwners:a.py", "ask.worstFile:b.py", "ask.decision:Use SQLite", "ask.trend"]);
  });

  it("skips a worst file that is also the hotspot and tops up with generic openers", () => {
    expect(overviewAskSuggestions(summary({ hotspot: "a.py", worst: "a.py" }), t)).toEqual([
      "ask.hotspotOwners:a.py",
      "ask.suggestion1",
      "ask.suggestion2",
    ]);
  });

  it("clips a long decision title", () => {
    const [q] = overviewAskSuggestions(summary({ decision: "x".repeat(80) }), t);
    expect(q!.length).toBeLessThan(80);
    expect(q!.endsWith("…")).toBe(true);
  });
});
