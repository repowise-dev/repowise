import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type Camera, type Rect, type Viewport, fitRoot, worldRectToScreen } from "../../src/zoom/camera";
import type { PickEntry } from "../../src/zoom/draw-tree";
import type { ExternalLane } from "../../src/zoom/externals";
import type { ZoomRenderer } from "../../src/zoom/renderer";
import type { ZoomScene } from "../../src/zoom/scene";
import type { ZoomNode } from "../../src/zoom/types";
import { ZoomActions } from "../../src/zoom/zoom-actions";

function node(id: string, parent: string | null, children: string[] = []): ZoomNode {
  return { id, parent_id: parent, children, name: id } as ZoomNode;
}

// a sits left, b sits under a 332px sheet on a 1000px-wide canvas at scale 1000
const rects = new Map<string, Rect>([
  ["root", { x: 0, y: 0, w: 1, h: 1 }],
  ["a", { x: 0.05, y: 0.4, w: 0.2, h: 0.1 }],
  ["b", { x: 0.75, y: 0.4, w: 0.2, h: 0.1 }],
]);
const nodes = new Map<string, ZoomNode>([
  ["root", node("root", null, ["a", "b"])],
  ["a", node("a", "root")],
  ["b", node("b", "root")],
]);
const scene: ZoomScene = { rootId: "root", nodes, worldRects: rects, relationsByParent: new Map(), drawChildren: new Map(), externalLanes: new Map(), laidOutCount: 3 };

function setup(selectionInset = 332, hit: PickEntry = { id: "b", rect: rects.get("b")!, depth: 1 }) {
  let cam: Camera = { cx: 0.5, cy: 0.5, scale: 1000 };
  let vp: Viewport = { w: 1000, h: 800 };
  const view = {
    setCam: (c: Camera) => (cam = c),
    setWidth: (w: number) => (vp = { ...vp, w }),
    vp: () => vp,
  };
  const renderer = {
    getViewport: () => vp,
    setInsetRight: vi.fn((px: number) => {
      vp = { ...vp, insetRight: px };
    }),
    getTargetCamera: () => cam,
    getCamera: () => cam,
    getFocusChain: () => [nodes.get("root")!],
    animateTo: vi.fn(),
    frameNode: vi.fn(() => true),
    setSelected: vi.fn(),
    pick: vi.fn(() => hit.id),
    pickEntry: vi.fn(() => hit),
  };
  const onSelect = vi.fn();
  const actions = new ZoomActions({
    renderer: () => renderer as unknown as ZoomRenderer,
    scene: () => scene,
    node: (id) => nodes.get(id),
    insetRight: () => selectionInset,
    reducedMotion: () => false,
    onSelect,
  });
  return { actions, renderer, onSelect, view };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("ZoomActions", () => {
  it("pans an off-screen sibling into view when the keys step to it", () => {
    const { actions, renderer, onSelect, view } = setup(0);
    // Zoomed onto a: b sits off the right edge.
    view.setCam({ cx: 0.15, cy: 0.45, scale: 3000 });
    actions.select("a");
    expect(renderer.animateTo).not.toHaveBeenCalled();
    expect(actions.step("right")).toBe(true);
    expect(onSelect).toHaveBeenLastCalledWith(nodes.get("b"));
    expect(renderer.animateTo).toHaveBeenCalledTimes(1);
    const to = renderer.animateTo.mock.calls[0]![0] as Camera;
    expect(to.scale).toBe(3000);
    const r = worldRectToScreen(to, view.vp(), rects.get("b")!);
    expect(r.x).toBeGreaterThanOrEqual(16 - 1e-6);
    expect(r.x + r.w).toBeLessThanOrEqual(1000 - 16 + 1e-6);
  });

  it("pans a host-selected card clear of the sheet right away", () => {
    const { actions, renderer, onSelect } = setup();
    actions.select("b");
    expect(onSelect).toHaveBeenCalledWith(nodes.get("b"));
    expect(renderer.animateTo).toHaveBeenCalledTimes(1);
  });

  it("pans a clicked card out from under the sheet sideways only", () => {
    const { actions, renderer, view } = setup();
    // b sits under the sheet and is cut off at the bottom edge.
    view.setCam({ cx: 0.5, cy: 0.1, scale: 1000 });
    actions.selectAt(0, 0);
    vi.runAllTimers();
    expect(renderer.animateTo).toHaveBeenCalledTimes(1);
    const to = renderer.animateTo.mock.calls[0]![0] as Camera;
    expect(to.cy).toBe(0.1);
    expect(to.cx).toBeGreaterThan(0.5);
  });

  it("leaves a card that is already clear where it is", () => {
    const { actions, renderer } = setup();
    actions.select("a");
    expect(renderer.animateTo).not.toHaveBeenCalled();
  });

  it("covers the sheet's strip while a node is selected, and frees it on close", () => {
    const { actions, renderer } = setup();
    actions.select("a");
    expect(renderer.setInsetRight).toHaveBeenLastCalledWith(332);
    actions.select(null);
    expect(renderer.setInsetRight).toHaveBeenLastCalledWith(0);
  });

  it("re-frames a view fitted beside the sheet when the sheet closes", () => {
    const { actions, renderer, view } = setup();
    actions.select("a");
    view.setCam(fitRoot(view.vp(), rects.get("root")!));
    renderer.animateTo.mockClear();
    actions.select(null);
    expect(renderer.animateTo).toHaveBeenCalledWith(fitRoot({ w: 1000, h: 800, insetRight: 0 }, rects.get("root")!), expect.anything());
  });

  it("drops the sheet's strip when a resize leaves too little beside it", () => {
    const { actions, renderer, view } = setup();
    actions.select("a");
    view.setWidth(500);
    actions.refitInset();
    expect(renderer.setInsetRight).toHaveBeenLastCalledWith(0);
  });

  it("Enter right after a click lands the selection before diving", () => {
    const { actions, renderer, onSelect } = setup();
    actions.selectAt(850, 400);
    actions.enter();
    expect(onSelect).toHaveBeenCalledWith(nodes.get("b"));
    expect(renderer.setInsetRight).toHaveBeenLastCalledWith(332);
    expect(renderer.frameNode).toHaveBeenCalledWith("b", expect.anything());
  });

  it("dispose drops a pending click selection", () => {
    const { actions, onSelect } = setup();
    actions.selectAt(850, 400);
    actions.dispose();
    vi.advanceTimersByTime(1000);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("ignores a sheet that would leave too little canvas beside it", () => {
    const { actions, view } = setup(900);
    actions.select("a");
    expect(view.vp().insetRight ?? 0).toBe(0);
  });

  it("rings a clicked card at once but opens the sheet after the double-click window", () => {
    const { actions, renderer, onSelect } = setup();
    actions.selectAt(850, 400);
    expect(renderer.setSelected).toHaveBeenLastCalledWith("b");
    expect(onSelect).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(onSelect).toHaveBeenCalledWith(nodes.get("b"));
    expect(renderer.animateTo).toHaveBeenCalledTimes(1);
  });

  it("a double-click dives and leaves the selection as it was", () => {
    const { actions, renderer, onSelect } = setup();
    actions.select("a");
    onSelect.mockClear();
    actions.selectAt(850, 400);
    actions.selectAt(850, 400);
    actions.diveAt(850, 400);
    vi.advanceTimersByTime(1000);
    expect(onSelect).not.toHaveBeenCalled();
    expect(renderer.setSelected).toHaveBeenLastCalledWith("a");
    expect(renderer.frameNode).toHaveBeenCalledWith("b", expect.anything());
  });

  it("a camera command keeps a pending click selection but drops its pan", () => {
    const { actions, renderer, onSelect } = setup();
    actions.selectAt(850, 400);
    actions.zoomBy(1.2);
    vi.advanceTimersByTime(1000);
    expect(onSelect).toHaveBeenCalledWith(nodes.get("b"));
    expect(renderer.animateTo).toHaveBeenCalledTimes(1); // the zoom step only
  });

  it("a double-click on an external card dives into the child importing it most", () => {
    const lane = {
      byTarget: new Map([["ext:db", [{ sourceId: "a", edgeCount: 9 }, { sourceId: "b", edgeCount: 2 }]]]),
    } as unknown as ExternalLane;
    const { actions, renderer } = setup(332, { id: "ext:db", rect: rects.get("a")!, depth: 1, lane });
    actions.diveAt(100, 100);
    expect(renderer.frameNode).toHaveBeenCalledWith("a", expect.anything());
  });

  it("Escape clears the selection first, then climbs to the overview", () => {
    const { actions, renderer, onSelect } = setup();
    actions.select("a");
    actions.escape();
    expect(onSelect).toHaveBeenLastCalledWith(null);
    expect(renderer.animateTo).not.toHaveBeenCalled();
    actions.escape();
    expect(renderer.animateTo).toHaveBeenCalledTimes(1);
  });

  it("drops a selection whose node vanished from new data", () => {
    const { actions, onSelect } = setup();
    actions.select("a");
    nodes.delete("a");
    actions.resync();
    expect(onSelect).toHaveBeenLastCalledWith(null);
    nodes.set("a", node("a", "root"));
  });
});
