import { describe, it, expect } from "vitest";
import { activateEdge, activateNode } from "../../src/workspace/system-map/activate";
import type { DrawnNode } from "../../src/workspace/system-map/level-types";

const service: DrawnNode = { type: "service", id: "callers:web", serviceId: "web", parentId: null, column: "callers" };
const group: DrawnNode = {
  type: "group",
  id: "group:tag:a",
  groupId: "tag:a",
  label: "A",
  source: "tag",
  open: false,
  serviceIds: ["x", "y"],
  repoCount: 2,
  linksIn: 0,
  linksOut: 0,
  linksInside: 0,
};

describe("activateNode", () => {
  it("selects a service on a single click", () => {
    expect(activateNode(service, "click")).toEqual({ kind: "select", selection: { type: "node", id: "web" } });
  });

  it("focuses a service on a double-click or Enter, by its service id", () => {
    expect(activateNode(service, "double")).toEqual({ kind: "focus", serviceId: "web" });
    expect(activateNode(service, "key")).toEqual({ kind: "focus", serviceId: "web" });
  });

  it("still opens a closed group on a double-click and selects it on a click", () => {
    expect(activateNode(group, "double")).toEqual({ kind: "open", id: "tag:a" });
    expect(activateNode(group, "click")).toEqual({ kind: "select", selection: { type: "group", id: "tag:a" } });
  });

  it("does nothing for an unknown id", () => {
    expect(activateNode(undefined, "key")).toBeNull();
    expect(activateEdge(undefined)).toBeNull();
  });

  it("opens a closed tray on a click", () => {
    const tray: DrawnNode = { type: "tray", id: "tray", open: false, serviceIds: ["z"] };
    expect(activateNode(tray, "click")).toEqual({ kind: "open", id: "tray" });
  });
});
