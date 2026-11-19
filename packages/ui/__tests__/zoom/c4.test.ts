import { describe, expect, it } from "vitest";

import {
  containerBadge,
  containerMembers,
  externalsReached,
  indexExternals,
} from "../../src/zoom/c4";
import { techName } from "../../src/zoom/tech-names";
import type { ZoomContainer, ZoomMap, ZoomNode } from "../../src/zoom/types";

function node(id: string, parent: string | null, containers: string[]): ZoomNode {
  return { id, parent_id: parent, container_ids: containers } as ZoomNode;
}

const containers = new Map<string, ZoomContainer>([
  ["pkg:web", { id: "pkg:web", name: "web", path: "web", kind: "web_app", technologies: [], file_count: 1 }],
  ["pkg:api", { id: "pkg:api", name: "api", path: "api", kind: "api", technologies: [], file_count: 1 }],
]);

describe("techName", () => {
  it("spells known technologies properly", () => {
    expect(techName("python")).toBe("Python");
    expect(techName("Fastapi")).toBe("FastAPI");
    expect(techName("Next")).toBe("Next.js");
    expect(techName("Sqlalchemy")).toBe("SQLAlchemy");
    expect(techName("typescript")).toBe("TypeScript");
  });

  it("keeps an unknown name's casing behind a leading capital", () => {
    expect(techName("Shell")).toBe("Shell");
    expect(techName("kotlin")).toBe("Kotlin");
    expect(techName("GraphiQL")).toBe("GraphiQL");
  });
});

describe("containerBadge", () => {
  const system = node("s", null, ["pkg:web", "pkg:api"]);

  it("names the container where a node narrows to one", () => {
    expect(containerBadge(node("l", "s", ["pkg:api"]), system, containers)).toBe("API");
    expect(containerBadge(node("w", "s", ["pkg:web"]), system, containers)).toBe("Web app");
  });

  it("keeps the kind label on a node spanning several", () => {
    const parent = node("p", null, ["pkg:web", "pkg:api", "pkg:x"]);
    expect(containerBadge(node("l", "p", ["pkg:web", "pkg:api"]), parent, containers)).toBeNull();
  });

  it("keeps the kind label where the container set repeats the parent's", () => {
    const layer = node("l", "s", ["pkg:api"]);
    expect(containerBadge(node("f", "l", ["pkg:api"]), layer, containers)).toBeNull();
    expect(containerBadge(node("n", "s", []), system, containers)).toBeNull();
  });
});

describe("containerMembers", () => {
  it("collects every node with files in the container", () => {
    const nodes = [
      node("s", null, ["pkg:web", "pkg:api"]),
      node("a", "s", ["pkg:api"]),
      node("w", "s", ["pkg:web"]),
    ];
    expect([...containerMembers(nodes, "pkg:api")].sort()).toEqual(["a", "s"]);
  });
});

describe("externalsReached", () => {
  const rel = (parent: string, source: string, target: string, n: number) => ({
    parent_id: parent,
    source_id: source,
    target_id: target,
    label: "imports",
    edge_count: n,
    coupling: "loose",
  });
  const ext = (id: string) => ({
    id,
    name: id.slice(4),
    display_name: id.slice(4),
    kind: "db",
    category: "service",
    io_kind: "db",
    ecosystem: "pypi",
    file_count: 1,
    edge_count: 1,
  });
  const map = {
    externals: [ext("ext:pg"), ext("ext:redis")],
    external_relations: [
      rel("root", "a", "ext:pg", 5),
      rel("root", "b", "ext:pg", 2),
      rel("root", "b", "ext:redis", 9),
      rel("a", "a1", "ext:pg", 5),
      rel("root", "a", "ext:unknown", 3),
    ],
  } as unknown as ZoomMap;
  const index = indexExternals(map);

  it("lists a node's externals from its own links, strongest first", () => {
    const b = externalsReached(index, node("b", "root", []));
    expect(b.map((r) => [r.external.id, r.edgeCount])).toEqual([
      ["ext:redis", 9],
      ["ext:pg", 2],
    ]);
  });

  it("sums the root's children for the root", () => {
    const root = externalsReached(index, node("root", null, []));
    expect(root.map((r) => [r.external.id, r.edgeCount])).toEqual([
      ["ext:redis", 9],
      ["ext:pg", 7],
    ]);
  });
});
