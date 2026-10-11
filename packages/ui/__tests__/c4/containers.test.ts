import { describe, it, expect } from "vitest";
import {
  buildContainers,
  enforceBoxBudget,
  getStandaloneNodeIds,
  MAX_VISIBLE_BOXES,
} from "../../src/c4/layout/containers";
import type { ArchNode, ArchEdge } from "../../src/c4/types";

function makeNode(overrides: Partial<ArchNode> & { id: string }): ArchNode {
  return {
    node_type: "file",
    name: overrides.id,
    file_path: null,
    line_range: null,
    summary: "",
    complexity: "simple",
    tags: [],
    language: null,
    pagerank: 0,
    pagerank_percentile: 0,
    betweenness: 0,
    in_degree: 0,
    out_degree: 0,
    community_id: null,
    is_entry_point: false,
    is_test: false,
    is_hotspot: false,
    is_dead: false,
    has_doc: false,
    primary_owner: null,
    primary_owner_pct: null,
    bus_factor: null,
    ...overrides,
  };
}

describe("buildContainers", () => {
  describe("folder strategy", () => {
    it("groups files with common directory together", () => {
      const nodes: ArchNode[] = [
        makeNode({ id: "src/api/app.py", file_path: "src/api/app.py" }),
        makeNode({ id: "src/api/routes.py", file_path: "src/api/routes.py" }),
        makeNode({ id: "src/core/models.py", file_path: "src/core/models.py" }),
        makeNode({ id: "src/core/logic.py", file_path: "src/core/logic.py" }),
      ];
      const containers = buildContainers(nodes, [], "folder");
      expect(containers).toHaveLength(2);
      const apiContainer = containers.find((c) => c.id === "dir:src/api");
      const coreContainer = containers.find((c) => c.id === "dir:src/core");
      expect(apiContainer).toBeDefined();
      expect(apiContainer!.childNodeIds).toHaveLength(2);
      expect(apiContainer!.label).toBe("api");
      expect(coreContainer).toBeDefined();
      expect(coreContainer!.childNodeIds).toHaveLength(2);
      expect(coreContainer!.label).toBe("core");
    });

    it("excludes nodes without file_path", () => {
      const nodes: ArchNode[] = [
        makeNode({ id: "src/api/app.py", file_path: "src/api/app.py" }),
        makeNode({ id: "src/api/routes.py", file_path: "src/api/routes.py" }),
        makeNode({ id: "concept-node", file_path: null }),
      ];
      const containers = buildContainers(nodes, [], "folder");
      const allIds = containers.flatMap((c) => c.childNodeIds);
      expect(allIds).not.toContain("concept-node");
    });
  });

  describe("community strategy", () => {
    it("groups files with same community_id", () => {
      const nodes: ArchNode[] = [
        makeNode({ id: "a", community_id: 1 }),
        makeNode({ id: "b", community_id: 1 }),
        makeNode({ id: "c", community_id: 2 }),
        makeNode({ id: "d", community_id: 2 }),
      ];
      const containers = buildContainers(nodes, [], "community");
      expect(containers).toHaveLength(2);
      const c1 = containers.find((c) => c.id === "community:1");
      const c2 = containers.find((c) => c.id === "community:2");
      expect(c1!.childNodeIds).toContain("a");
      expect(c1!.childNodeIds).toContain("b");
      expect(c2!.childNodeIds).toContain("c");
      expect(c2!.childNodeIds).toContain("d");
    });

    it("excludes nodes with null community_id", () => {
      const nodes: ArchNode[] = [
        makeNode({ id: "a", community_id: 1 }),
        makeNode({ id: "b", community_id: 1 }),
        makeNode({ id: "orphan", community_id: null }),
      ];
      const containers = buildContainers(nodes, [], "community");
      const allIds = containers.flatMap((c) => c.childNodeIds);
      expect(allIds).not.toContain("orphan");
    });
  });

  describe("auto strategy", () => {
    it("selects folder when >80% have file_path", () => {
      const nodes: ArchNode[] = [
        makeNode({ id: "src/a.py", file_path: "src/a.py" }),
        makeNode({ id: "src/b.py", file_path: "src/b.py" }),
        makeNode({ id: "src/c.py", file_path: "src/c.py" }),
        makeNode({ id: "src/d.py", file_path: "src/d.py" }),
        makeNode({ id: "src/e.py", file_path: "src/e.py" }),
        makeNode({ id: "concept", file_path: null, community_id: 1 }),
      ];
      const containers = buildContainers(nodes, [], "auto");
      const hasDirContainer = containers.some((c) => c.id.startsWith("dir:"));
      expect(hasDirContainer).toBe(true);
    });

    it("selects community when <80% have file_path", () => {
      const nodes: ArchNode[] = [
        makeNode({ id: "a", file_path: "src/a.py", community_id: 1 }),
        makeNode({ id: "b", file_path: null, community_id: 1 }),
        makeNode({ id: "c", file_path: null, community_id: 2 }),
        makeNode({ id: "d", file_path: null, community_id: 2 }),
        makeNode({ id: "e", file_path: null, community_id: 2 }),
      ];
      const containers = buildContainers(nodes, [], "auto");
      const hasCommunityContainer = containers.some((c) => c.id.startsWith("community:"));
      expect(hasCommunityContainer).toBe(true);
    });
  });

  describe("curated strategy", () => {
    const subGroups = [
      { id: "layer:ui:forms", name: "forms", node_ids: ["src/ui/form.tsx", "src/ui/input.tsx"] },
      { id: "layer:ui:tables", name: "tables", node_ids: ["src/ui/table.tsx", "src/ui/row.tsx"] },
    ];

    it("builds containers verbatim from sub-groups (id/name/members)", () => {
      const nodes = subGroups.flatMap((g) =>
        g.node_ids.map((id) => makeNode({ id, file_path: id })),
      );
      const containers = buildContainers(nodes, [], "curated", subGroups);
      expect(containers).toHaveLength(2);
      expect(containers[0]).toEqual({
        id: "layer:ui:forms",
        label: "forms",
        childNodeIds: ["src/ui/form.tsx", "src/ui/input.tsx"],
      });
      expect(containers[1]!.label).toBe("tables");
    });

    it("drops members hidden by upstream filters and flattens singletons", () => {
      // Only one forms file and both table files visible.
      const nodes = [
        makeNode({ id: "src/ui/form.tsx", file_path: "src/ui/form.tsx" }),
        makeNode({ id: "src/ui/table.tsx", file_path: "src/ui/table.tsx" }),
        makeNode({ id: "src/ui/row.tsx", file_path: "src/ui/row.tsx" }),
      ];
      const containers = buildContainers(nodes, [], "curated", subGroups);
      // forms collapsed to a singleton → flattened away; tables survives.
      expect(containers.map((c) => c.id)).toEqual(["layer:ui:tables"]);
    });

    it("falls back to heuristics when the layer has no sub-groups", () => {
      const nodes: ArchNode[] = [
        makeNode({ id: "src/api/app.py", file_path: "src/api/app.py" }),
        makeNode({ id: "src/api/routes.py", file_path: "src/api/routes.py" }),
      ];
      const containers = buildContainers(nodes, [], "curated", []);
      expect(containers.some((c) => c.id.startsWith("dir:"))).toBe(true);
    });

    it("falls back to heuristics when no sub-group survives filtering", () => {
      const nodes: ArchNode[] = [
        makeNode({ id: "src/api/app.py", file_path: "src/api/app.py" }),
        makeNode({ id: "src/api/routes.py", file_path: "src/api/routes.py" }),
      ];
      // Sub-groups reference nodes that are all filtered out.
      const containers = buildContainers(nodes, [], "curated", subGroups);
      expect(containers.some((c) => c.id.startsWith("dir:"))).toBe(true);
    });
  });

  describe("single-node container flattening", () => {
    it("eliminates containers with only 1 node", () => {
      const nodes: ArchNode[] = [
        makeNode({ id: "src/api/app.py", file_path: "src/api/app.py" }),
        makeNode({ id: "src/api/routes.py", file_path: "src/api/routes.py" }),
        makeNode({ id: "src/lone/only.py", file_path: "src/lone/only.py" }),
      ];
      const containers = buildContainers(nodes, [], "folder");
      const loneContainer = containers.find((c) => c.id === "dir:src/lone");
      expect(loneContainer).toBeUndefined();
    });
  });
});

describe("getStandaloneNodeIds", () => {
  it("returns node ids not in any container", () => {
    const nodes: ArchNode[] = [
      makeNode({ id: "a" }),
      makeNode({ id: "b" }),
      makeNode({ id: "c" }),
    ];
    const containers = [{ id: "c1", label: "C1", childNodeIds: ["a", "b"] }];
    const standalone = getStandaloneNodeIds(nodes, containers);
    expect(standalone).toEqual(["c"]);
  });
});

describe("enforceBoxBudget", () => {
  const pr = new Map<string, number>([
    ["hot1", 0.9], ["hot2", 0.8],
    ["mid1", 0.5], ["mid2", 0.4],
    ["cold1", 0.1], ["cold2", 0.05], ["cold3", 0.01],
  ]);
  const rank = (id: string) => pr.get(id) ?? 0;

  it("passes through when within budget", () => {
    const containers = [{ id: "c1", label: "C1", childNodeIds: ["hot1", "mid1"] }];
    const out = enforceBoxBudget(containers, ["hot2"], rank);
    expect(out.containers).toEqual(containers);
    expect(out.standaloneIds).toEqual(["hot2"]);
    expect(out.collapsedCount).toBe(0);
  });

  it("collapses the lowest-pagerank boxes into one '+N more' container", () => {
    const containers = [
      { id: "c-hot", label: "hot", childNodeIds: ["hot1", "hot2"] },
      { id: "c-cold", label: "cold", childNodeIds: ["cold1", "cold2"] },
    ];
    const standalone = ["mid1", "mid2", "cold3"];
    // Budget 4: keep the 3 strongest boxes, fold the rest.
    const out = enforceBoxBudget(containers, standalone, rank, 4);

    expect(out.containers.length + out.standaloneIds.length).toBeLessThanOrEqual(4);
    const overflow = out.containers.find((c) => c.id === "container:__overflow");
    expect(overflow).toBeDefined();
    expect(overflow!.label).toBe(`+${out.collapsedCount} more`);
    // The weakest boxes (cold container + cold3) live inside the overflow.
    expect(overflow!.childNodeIds).toEqual(
      expect.arrayContaining(["cold1", "cold2", "cold3"]),
    );
    // The strongest boxes survive untouched.
    expect(out.containers.some((c) => c.id === "c-hot")).toBe(true);
    expect(out.standaloneIds).toContain("mid1");
  });

  it("never exceeds the default budget", () => {
    const standalone = Array.from({ length: 40 }, (_, i) => `f${i}`);
    const out = enforceBoxBudget([], standalone, () => 0);
    expect(out.containers.length + out.standaloneIds.length).toBeLessThanOrEqual(
      MAX_VISIBLE_BOXES,
    );
    expect(out.collapsedCount).toBe(40 - (MAX_VISIBLE_BOXES - 1));
  });
});
