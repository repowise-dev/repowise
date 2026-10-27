import { describe, it, expect } from "vitest";
import { communityColorSlots } from "../../src/graph/community-colors";

const LABELS = [
  "ingestion",
  "web/components",
  "cli",
  "persistence",
  "health",
  "server/routers",
  "ui/graph",
  "workspace",
  "generation",
  "analysis",
  "mcp",
  "distill",
  "vscode",
  "types",
];

describe("communityColorSlots", () => {
  it("keeps a community's colour when a re-index renumbers it", () => {
    const before = LABELS.map((label, i) => ({ communityId: i, label, size: 200 - i * 10 }));
    // Same groups, ids shuffled (ids are size ranks, and two groups swapped).
    const after = before.map((e, i) => ({ ...e, communityId: (i * 5 + 3) % LABELS.length }));
    const a = communityColorSlots(before);
    const b = communityColorSlots(after);
    for (let i = 0; i < LABELS.length; i++) {
      expect(b.get(after[i]!.communityId)).toBe(a.get(before[i]!.communityId));
    }
  });

  it("gives the twelve largest communities twelve different colours", () => {
    const slots = communityColorSlots(
      LABELS.map((label, i) => ({ communityId: i, label, size: 500 - i })),
    );
    const top = new Set(LABELS.slice(0, 12).map((_, i) => slots.get(i)));
    expect(top.size).toBe(12);
  });

  it("ignores the member-count suffix a duplicate label carries", () => {
    const a = communityColorSlots([{ communityId: 4, label: "tests/unit (288)", size: 288 }]);
    const b = communityColorSlots([{ communityId: 9, label: "tests/unit (301)", size: 301 }]);
    expect(a.get(4)).toBe(b.get(9));
  });
});
