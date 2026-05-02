import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GraphFlow } from "../../src/graph/graph-flow.js";

// Mock @xyflow/react — the real implementation needs a measured DOM that
// jsdom doesn't provide; we just want to assert the shell composes the
// surrounding chrome correctly.
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="react-flow-mock">{children}</div>
  ),
  ReactFlowProvider: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  MiniMap: () => null,
  Controls: () => null,
  Background: () => null,
  BackgroundVariant: { Dots: "dots" },
  useReactFlow: () => ({ getNode: () => undefined, fitView: vi.fn() }),
}));

// elk-layout / use-elk-layout do real DOM work; stub them out.
vi.mock("../../src/graph/use-elk-layout.js", () => ({
  useModuleElkLayout: () => ({ nodes: [], edges: [], isLayouting: false }),
  useFileElkLayout: () => ({ nodes: [], edges: [], isLayouting: false }),
}));

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
