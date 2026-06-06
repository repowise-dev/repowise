/**
 * Orientation-first ProjectOverview (viewer plan C-1): summary, Start tour
 * CTA, clickable entry points, layer stack mini-map — stats below the fold.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { ProjectOverview } from "../../src/c4/panels/ProjectOverview";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import { createMockView } from "./fixtures";

const store = useArchitectureStore;

beforeEach(() => {
  store.setState(store.getInitialState());
});

function setCuratedView() {
  const view = createMockView({
    entry_points: ["src/app.py", "src/routes.py"],
  });
  store.getState().setView(view);
  return view;
}

describe("ProjectOverview (orientation-first)", () => {
  it("leads with Start tour, entry points and the layer stack", () => {
    setCuratedView();
    const { getByText, getByLabelText } = render(<ProjectOverview />);

    const tourButton = getByText(/Start tour \(3 steps\)/);
    const entryPoint = getByLabelText("Open entry point src/app.py");
    const layerRow = getByLabelText("Explore layer API");
    const stats = getByText("Stats");

    // Orientation content appears above the stats fold in DOM order.
    expect(
      tourButton.compareDocumentPosition(stats) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      entryPoint.compareDocumentPosition(stats) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      layerRow.compareDocumentPosition(stats) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("clicking an entry point selects (and auto-drills to) the node", () => {
    setCuratedView();
    const { getByLabelText } = render(<ProjectOverview />);

    fireEvent.click(getByLabelText("Open entry point src/app.py"));
    expect(store.getState().selectedNodeId).toBe("src/app.py");
    expect(store.getState().activeLayerId).toBe("layer:api");
  });

  it("clicking a layer row drills into the layer", () => {
    setCuratedView();
    const { getByLabelText } = render(<ProjectOverview />);

    fireEvent.click(getByLabelText("Explore layer Core"));
    expect(store.getState().activeLayerId).toBe("layer:core");
  });

  it("orders the layer stack by curated display_order", () => {
    const view = createMockView();
    // Reverse the natural order: Core(0) above API(1).
    view.layers[0]!.display_order = 1;
    view.layers[1]!.display_order = 0;
    store.getState().setView(view);
    const { getByLabelText } = render(<ProjectOverview />);

    const api = getByLabelText("Explore layer API");
    const core = getByLabelText("Explore layer Core");
    expect(
      core.compareDocumentPosition(api) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("starting the tour from the CTA activates it", () => {
    setCuratedView();
    const { getByText } = render(<ProjectOverview />);

    fireEvent.click(getByText(/Start tour/));
    expect(store.getState().tourActive).toBe(true);
  });

  it("hides entry points section for uncurated repos", () => {
    store.getState().setView(createMockView());
    const { queryByText } = render(<ProjectOverview />);
    expect(queryByText("Entry points")).toBeNull();
  });
});
