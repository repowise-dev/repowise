/**
 * The heatmap's axis labels.
 *
 * Labels used to be cut with `slice(9)` — the length of `"external:"` spelled
 * as a number — so any other prefix lost nine characters off the front of a
 * real path. It routes through the shared helper now, and this pins that a
 * module id we do own is not trimmed at all.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import type { ModuleGraph } from "@repowise-dev/types/graph";
import { DependencyHeatmap } from "../../src/dashboard/dependency-heatmap";

function graphOf(moduleIds: string[]): ModuleGraph {
  return {
    nodes: moduleIds.map((module_id) => ({
      module_id,
      file_count: 1,
      loc: 10,
      language: "python",
    })),
    edges: moduleIds.slice(1).map((module_id) => ({
      source: moduleIds[0]!,
      target: module_id,
      weight: 1,
    })),
  } as unknown as ModuleGraph;
}

describe("DependencyHeatmap labels", () => {
  it("strips a dependency's prefix but not a real path", () => {
    const { container } = render(
      <DependencyHeatmap moduleGraph={graphOf(["packages/core", "external:react"])} />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("react");
    expect(text).not.toContain("external:");
    // "packages/core" keeps its last segment whole — nine characters off the
    // front would have left "s/core".
    expect(text).toContain("core");
    expect(text).not.toContain("s/core");
  });

  it("does not trim a short module id into nothing", () => {
    const { container } = render(<DependencyHeatmap moduleGraph={graphOf(["api", "db"])} />);
    const text = container.textContent ?? "";
    expect(text).toContain("api");
    expect(text).toContain("db");
  });
});
