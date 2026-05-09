import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GraphFlow } from "../../src/graph/graph-flow.js";


describe("GraphFlow shell", () => {
  it("renders the empty state when no nodes are layouted", () => {
    render(
      <GraphFlow
        moduleGraph={undefined}
        isLoadingModuleGraph={false}
        fullGraph={undefined}
        isLoadingFullGraph={false}
        architectureGraph={undefined}
        isLoadingArchitectureGraph={false}
        deadCodeGraph={undefined}
        isLoadingDeadCodeGraph={false}
        hotFilesGraph={undefined}
        isLoadingHotFilesGraph={false}
      />,
    );
    expect(screen.getByText("No graph data")).toBeTruthy();
  });
});
