import { describe, it, expect } from "vitest";
import {
  LABEL_H,
  bezierNormal,
  bezierPoint,
  edgeLabelLayout,
  layoutEdgeLabels,
  pairLabels,
  verbSummary,
} from "../../src/zoom/edge-labels";
import type { EdgeRoute, RoutedEdge } from "../../src/zoom/edges";

const VP = { x: 0, y: 0, w: 1000, h: 800 };
const measure = (s: string) => s.length * 6;

function straight(x0: number, y0: number, x1: number, y1: number): EdgeRoute {
  return {
    start: { x: x0, y: y0 },
    c1: { x: x0 + (x1 - x0) / 3, y: y0 + (y1 - y0) / 3 },
    c2: { x: x0 + (2 * (x1 - x0)) / 3, y: y0 + (2 * (y1 - y0)) / 3 },
    end: { x: x1, y: y1 },
    endAngle: 0,
  };
}

function edge(s: string, t: string, label: string, n: number, route: EdgeRoute): RoutedEdge {
  return { id: `${s}>${t}`, sourceId: s, targetId: t, coupling: "loose", edgeCount: n, label, route };
}

describe("bezier helpers", () => {
  it("finds the midpoint and a unit normal", () => {
    const r = straight(0, 0, 100, 0);
    expect(bezierPoint(r, 0.5)).toEqual({ x: 50, y: 0 });
    const nrm = bezierNormal(r, 0.5);
    expect(Math.hypot(nrm.x, nrm.y)).toBeCloseTo(1);
    expect(nrm.x).toBeCloseTo(0);
  });
});

describe("verbSummary", () => {
  it("orders verbs by count, ties by name, and sums the weight", () => {
    const verbs = new Map([["imports", 5], ["calls", 1300], ["uses", 5]]);
    expect(verbSummary(verbs)).toEqual({ text: "calls 1,300 · imports 5 · uses 5", weight: 1310 });
  });
});

describe("pairLabels", () => {
  it("merges every verb between one pair, both directions, strongest verb first", () => {
    const r = straight(0, 0, 100, 0);
    const pairs = pairLabels([
      edge("a", "b", "imports", 5, r),
      edge("b", "a", "calls", 13, r),
      edge("a", "c", "uses", 2, r),
    ]);
    expect(pairs.map((p) => p.text)).toEqual(["calls 13 · imports 5", "uses 2"]);
  });
});

describe("layoutEdgeLabels", () => {
  it("places a label on its arrow's midpoint when there is room", () => {
    const [placed] = layoutEdgeLabels(
      pairLabels([edge("a", "b", "calls", 3, straight(100, 400, 500, 400))]),
      measure,
      VP,
    );
    expect(placed!.rect.x + placed!.rect.w / 2).toBeCloseTo(300);
    expect(placed!.rect.y + placed!.rect.h / 2).toBeCloseTo(400);
  });

  it("nudges colliding labels apart instead of stacking them", () => {
    // Two arrows sharing one midpoint.
    const placed = layoutEdgeLabels(
      pairLabels([
        edge("a", "b", "calls", 3, straight(100, 400, 500, 400)),
        edge("c", "d", "imports", 2, straight(100, 400, 500, 400)),
      ]),
      measure,
      VP,
    );
    expect(placed).toHaveLength(2);
    const [p, q] = placed;
    const apart =
      p!.rect.y + LABEL_H <= q!.rect.y ||
      q!.rect.y + LABEL_H <= p!.rect.y ||
      p!.rect.x + p!.rect.w <= q!.rect.x ||
      q!.rect.x + q!.rect.w <= p!.rect.x;
    expect(apart).toBe(true);
  });

  it("moves a label off a card when a clear spot is near", () => {
    const pairs = pairLabels([edge("a", "b", "calls", 3, straight(100, 400, 500, 400))]);
    const card = { x: 250, y: 390, w: 100, h: 20 };
    const [moved] = layoutEdgeLabels(pairs, measure, VP, [card]);
    const r = moved!.rect;
    const hits = r.x < card.x + card.w && card.x < r.x + r.w && r.y < card.y + card.h && card.y < r.y + r.h;
    expect(hits).toBe(false);
  });

  it("straddles the padding of two cards a short gap apart, never their text", () => {
    const pairs = pairLabels([edge("a", "b", "calls", 3, straight(290, 400, 330, 400))]);
    const left = { x: 0, y: 300, w: 290, h: 200 };
    const right = { x: 330, y: 300, w: 290, h: 200 };
    const [placed] = layoutEdgeLabels(pairs, measure, VP, [left, right]);
    expect(placed!.rect.x).toBeGreaterThanOrEqual(290 - 12);
    expect(placed!.rect.x + placed!.rect.w).toBeLessThanOrEqual(330 + 12);
  });

  it("drops a label that would only fit over card text", () => {
    const pairs = pairLabels([edge("a", "b", "calls", 3, straight(100, 400, 500, 400))]);
    expect(layoutEdgeLabels(pairs, measure, VP, [{ ...VP }])).toEqual([]);
  });

  it("drops a label it cannot fit inside the viewport", () => {
    const placed = layoutEdgeLabels(
      pairLabels([edge("a", "b", "calls", 3, straight(0, 2, 10, 2))]),
      measure,
      { x: 0, y: 0, w: 12, h: 12 },
    );
    expect(placed).toEqual([]);
  });
});

describe("edgeLabelLayout", () => {
  it("reuses the layout while its key is unchanged and redoes it when the key moves", () => {
    const edges = [edge("a", "b", "calls", 3, straight(100, 400, 500, 400))];
    const cam = {};
    let calls = 0;
    const counting = (s: string) => {
      calls++;
      return measure(s);
    };
    const first = edgeLabelLayout([cam, "a"], edges, counting, VP);
    const again = edgeLabelLayout([cam, "a"], edges, counting, VP);
    expect(again).toBe(first);
    expect(calls).toBe(1);
    edgeLabelLayout([cam, "b"], edges, counting, VP);
    expect(calls).toBe(2);
  });
});
