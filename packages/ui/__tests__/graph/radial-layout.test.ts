import { describe, it, expect } from "vitest";
import {
  computeRadialLayout,
  hubSizeFromMembers,
  type RadialCommunityInput,
} from "../../src/graph/sigma/radial-layout";

function makeCommunities(n: number): RadialCommunityInput[] {
  return Array.from({ length: n }, (_, i) => ({
    community_id: i,
    member_count: (n - i) * 3 + 1, // varied sizes
    avg_pagerank: (n - i) / n, // descending centrality
  }));
}

function noNaN(x: number): boolean {
  return Number.isFinite(x);
}

describe("computeRadialLayout", () => {
  it("handles an empty community list without crashing", () => {
    const r = computeRadialLayout([]);
    expect(r.hubs.size).toBe(0);
    expect(r.satellites.size).toBe(0);
    expect(r.core).toEqual({ x: 0, y: 0 });
  });

  it("places a single community on the inner ring with no NaNs", () => {
    const r = computeRadialLayout(makeCommunities(1));
    expect(r.hubs.size).toBe(1);
    expect(r.rings.get(0)).toBe(1);
    const pos = r.hubs.get(0)!;
    expect(noNaN(pos.x) && noNaN(pos.y)).toBe(true);
  });

  it("is deterministic: same input → identical positions across runs", () => {
    const input = makeCommunities(9);
    const a = computeRadialLayout(input);
    const b = computeRadialLayout(input);
    for (const [cid, pos] of a.hubs) {
      expect(b.hubs.get(cid)).toEqual(pos);
    }
    expect(a.ringRadii).toEqual(b.ringRadii);
  });

  it("is order-independent (stable-sorted by community_id)", () => {
    const input = makeCommunities(9);
    const shuffled = [...input].reverse();
    const a = computeRadialLayout(input);
    const b = computeRadialLayout(shuffled);
    for (const [cid, pos] of a.hubs) {
      expect(b.hubs.get(cid)).toEqual(pos);
    }
  });

  it("assigns rings by tercile of rank (inner/middle/outer)", () => {
    const r = computeRadialLayout(makeCommunities(9));
    const ringCounts = { 1: 0, 2: 0, 3: 0 };
    for (const ring of r.rings.values()) ringCounts[ring] += 1;
    // 9 communities → 3 per ring.
    expect(ringCounts[1]).toBe(3);
    expect(ringCounts[2]).toBe(3);
    expect(ringCounts[3]).toBe(3);
  });

  it("ranks higher avg_pagerank onto the inner ring", () => {
    const r = computeRadialLayout(makeCommunities(9));
    // community_id 0 has the highest avg_pagerank → inner ring.
    expect(r.rings.get(0)).toBe(1);
    // community_id 8 has the lowest → outer ring.
    expect(r.rings.get(8)).toBe(3);
  });

  it("places hubs at radii matching their ring", () => {
    const r = computeRadialLayout(makeCommunities(9));
    for (const [cid, pos] of r.hubs) {
      const ring = r.rings.get(cid)!;
      const dist = Math.hypot(pos.x, pos.y);
      expect(dist).toBeCloseTo(r.ringRadii[ring - 1]!, 5);
    }
  });

  it("separates the largest hubs angularly within a ring", () => {
    // n=4 → tercile=2 → ring 1 holds the two highest-pagerank communities.
    // After relaxation those two should sit on opposite slots (~180° apart).
    const input: RadialCommunityInput[] = [
      { community_id: 0, member_count: 100, avg_pagerank: 0.95 },
      { community_id: 1, member_count: 90, avg_pagerank: 0.9 },
      { community_id: 2, member_count: 5, avg_pagerank: 0.2 },
      { community_id: 3, member_count: 4, avg_pagerank: 0.1 },
    ];
    const r = computeRadialLayout(input);
    // Both top communities land on the inner ring.
    expect(r.rings.get(0)).toBe(1);
    expect(r.rings.get(1)).toBe(1);
    const a = r.hubs.get(0)!;
    const b = r.hubs.get(1)!;
    const angA = Math.atan2(a.y, a.x);
    const angB = Math.atan2(b.y, b.x);
    let diff = Math.abs(angA - angB) % (2 * Math.PI);
    if (diff > Math.PI) diff = 2 * Math.PI - diff;
    // Two slots in a 2-member ring → ~180° apart.
    expect(diff).toBeGreaterThan(Math.PI / 2);
  });

  it("produces no NaN positions for a larger graph", () => {
    const r = computeRadialLayout(makeCommunities(25));
    for (const pos of r.hubs.values()) {
      expect(noNaN(pos.x) && noNaN(pos.y)).toBe(true);
    }
  });

  it("places satellites around their hub when members are given", () => {
    const input = makeCommunities(3);
    const members = new Map<number, string[]>([
      [0, ["a.ts", "b.ts", "c.ts"]],
      [1, ["d.ts"]],
    ]);
    const r = computeRadialLayout(input, members);
    expect(r.satellites.size).toBe(4);
    // Satellites of community 0 should sit near hub 0.
    const hub0 = r.hubs.get(0)!;
    const sat = r.satellites.get("a.ts")!;
    expect(noNaN(sat.x) && noNaN(sat.y)).toBe(true);
    const dist = Math.hypot(sat.x - hub0.x, sat.y - hub0.y);
    expect(dist).toBeLessThan(200);
  });

  it("satellite placement is deterministic", () => {
    const input = makeCommunities(2);
    const members = new Map<number, string[]>([[0, ["x.ts", "y.ts"]]]);
    const a = computeRadialLayout(input, members);
    const b = computeRadialLayout(input, members);
    expect(a.satellites.get("x.ts")).toEqual(b.satellites.get("x.ts"));
    expect(a.satellites.get("y.ts")).toEqual(b.satellites.get("y.ts"));
  });
});

describe("hubSizeFromMembers", () => {
  it("clamps to the 14–32 sigma-unit range", () => {
    expect(hubSizeFromMembers(0)).toBeGreaterThanOrEqual(14);
    expect(hubSizeFromMembers(1)).toBeGreaterThanOrEqual(14);
    expect(hubSizeFromMembers(100000)).toBeLessThanOrEqual(32);
  });

  it("grows monotonically with member count", () => {
    expect(hubSizeFromMembers(50)).toBeGreaterThan(hubSizeFromMembers(2));
  });
});
