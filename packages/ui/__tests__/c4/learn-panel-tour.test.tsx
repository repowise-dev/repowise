/**
 * Tour wiring end-to-end (viewer plan C-2): LearnPanel renders the curated
 * step fields (reason as body, layer pill, kind icon), the canvas follows
 * the step's layer/sub-group, and the highlighted node carries a numbered
 * step badge. Curated text is sentinel-tagged so we know the rendered
 * content came from the artifact, not a heuristic.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { act, render, renderHook, waitFor } from "@testing-library/react";
import { LearnPanel } from "../../src/c4/panels/LearnPanel";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import { useArchitectureLayout } from "../../src/c4/hooks/use-architecture-layout";
import { createMockView } from "./fixtures";
import type { ArchitectureView } from "../../src/c4/types";

const store = useArchitectureStore;

beforeEach(() => {
  store.setState(store.getInitialState());
});

function createCuratedTourView(): ArchitectureView {
  const base = createMockView();
  return {
    ...base,
    tour: [
      {
        order: 1,
        title: "app.py",
        description: "",
        node_ids: ["src/app.py"],
        target_path: "src/app.py",
        layer_id: "layer:api",
        reason: "SENTINEL-REASON: top of the stack, start here.",
        depth: 0,
        kind: "code",
        page_type: "file_page",
      },
      {
        order: 2,
        title: "models.py",
        description: "",
        node_ids: ["src/models.py"],
        target_path: "src/models.py",
        layer_id: "layer:core",
        reason: "SENTINEL-REASON: foundational layer.",
        depth: 1,
        kind: "code",
        page_type: "file_page",
      },
    ],
  };
}

describe("LearnPanel with curated tour steps", () => {
  it("renders reason as the step body, with layer pill and kind icon", () => {
    act(() => {
      store.getState().setView(createCuratedTourView());
      store.getState().startTour();
    });
    const { getByText, getByLabelText } = render(<LearnPanel />);

    expect(getByText("SENTINEL-REASON: top of the stack, start here.")).toBeDefined();
    expect(getByText("API")).toBeDefined(); // layer pill
    expect(getByLabelText("Code step")).toBeDefined(); // kind icon
  });

  it("falls back to description for legacy LLM steps", () => {
    act(() => {
      store.getState().setView(createMockView()); // legacy fixture tour
      store.getState().startTour();
    });
    const { getByText } = render(<LearnPanel />);
    expect(getByText("Start here")).toBeDefined();
  });
});

describe("canvas follows the tour", () => {
  it("each step drills the canvas to the step's layer", () => {
    act(() => {
      store.getState().setView(createCuratedTourView());
      store.getState().startTour();
    });
    expect(store.getState().activeLayerId).toBe("layer:api");

    act(() => {
      store.getState().nextTourStep();
    });
    expect(store.getState().activeLayerId).toBe("layer:core");
    expect(store.getState().selectedNodeId).toBe("src/models.py");
  });

  it("uses the curated layer_id when the step's node is not in the graph", () => {
    const view = createCuratedTourView();
    view.tour[0]!.node_ids = ["ghost/file.py"];
    view.tour[0]!.target_path = "ghost/file.py";
    act(() => {
      store.getState().setView(view);
      store.getState().startTour();
    });
    expect(store.getState().navigationLevel).toBe("layer-detail");
    expect(store.getState().activeLayerId).toBe("layer:api");
  });

  it("puts a numbered step badge on the highlighted node", async () => {
    act(() => {
      store.getState().setView(createCuratedTourView());
      store.getState().startTour();
      store.getState().nextTourStep(); // step 2 → src/models.py in layer:core
    });
    const { result } = renderHook(() => useArchitectureLayout());

    await waitFor(() => {
      expect(result.current.nodes.length).toBeGreaterThan(0);
    });

    const target = result.current.nodes.find((n) => n.id === "src/models.py")!;
    expect((target.data as { tourStepNumber?: number }).tourStepNumber).toBe(2);
    const other = result.current.nodes.find((n) => n.id !== "src/models.py" && n.type === "archFile");
    if (other) {
      expect((other.data as { tourStepNumber?: number }).tourStepNumber).toBeUndefined();
    }
  });
});
