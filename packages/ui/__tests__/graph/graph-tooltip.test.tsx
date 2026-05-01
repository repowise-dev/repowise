import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GraphTooltip } from "../../src/graph/graph-tooltip.js";

describe("GraphTooltip", () => {
  it("renders file metadata for a file node", () => {
    render(
      <GraphTooltip
        nodeId="src/foo/bar.ts"
        nodeType="fileNode"
        data={{
          label: "bar.ts",
          fullPath: "src/foo/bar.ts",
          language: "typescript",
          symbolCount: 3,
          pagerank: 0.005,
          betweenness: 0.002,
          communityId: 1,
          isTest: false,
          isEntryPoint: false,
          hasDoc: false,
        }}
        x={100}
        y={100}
        onClose={vi.fn()}
        onViewDocs={vi.fn()}
      />,
    );
    expect(screen.getByText("bar.ts")).toBeTruthy();
    expect(screen.getByText("typescript")).toBeTruthy();
  });

  it("renders module metadata for a moduleGroup node", () => {
    render(
      <GraphTooltip
        nodeId="src/foo"
        nodeType="moduleGroup"
        data={{
          label: "foo",
          fullPath: "src/foo",
          fileCount: 5,
          symbolCount: 12,
          avgPagerank: 0.001,
          docCoveragePct: 0.6,
        }}
        x={50}
        y={50}
        onClose={vi.fn()}
        onViewDocs={vi.fn()}
      />,
    );
    expect(screen.getByText("Doc coverage")).toBeTruthy();
    expect(screen.getByText("60%")).toBeTruthy();
  });
});
