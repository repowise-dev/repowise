// @vitest-environment jsdom

/**
 * The Knowledge Graph's `Map | C4 diagram` switch: `?view=c4` mounts the C4
 * diagram and not the zoom canvas, an absent `?view=` keeps the map, and the
 * switch writes the param (clearing it for the default). The two surfaces are
 * stubbed: this is about which one the page mounts.
 */

import * as React from "react";
import { Suspense } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  queryValues: {} as Record<string, unknown>,
  setView: vi.fn(),
}));

vi.mock("nuqs", () => {
  const parser = (defaultValue: unknown = null) => ({
    defaultValue,
    withDefault(value: unknown) {
      return { ...parser(value), withOptions: () => parser(value) };
    },
    withOptions() {
      return parser(defaultValue);
    },
  });
  return {
    parseAsString: parser(),
    parseAsStringLiteral: () => parser(),
    useQueryState: (name: string, value: { defaultValue?: unknown }) => [
      name in mocks.queryValues ? mocks.queryValues[name] : value?.defaultValue ?? null,
      name === "view" ? mocks.setView : vi.fn(),
    ],
  };
});

vi.mock("next/dynamic", () => ({
  default: () => (props: { repoId: string }) => <div data-testid="c4-view">c4:{props.repoId}</div>,
}));

vi.mock("@/lib/hooks/use-graph", () => ({
  useZoomMap: () => ({ zoomMap: undefined, error: undefined, isLoading: true, retry: vi.fn() }),
}));

vi.mock("@/components/zoom/zoom-canvas-state", () => ({
  ZOOM_FRAME: "frame",
  ZoomCanvasSkeleton: () => <div data-testid="map-skeleton" />,
  ZoomCanvasError: () => <div data-testid="map-error" />,
}));

vi.mock("@/components/zoom/zoom-export-button", () => ({
  ZoomExportButton: () => <button type="button">Structurizr DSL</button>,
}));

import KnowledgeGraphPage from "./page";

afterEach(cleanup);

describe("Knowledge Graph view switch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.queryValues = {};
  });

  // `use(params)` suspends the first render, so mount inside a boundary.
  const mount = async (view?: string) => {
    mocks.queryValues = view === undefined ? {} : { view };
    await act(async () => {
      render(
        <Suspense fallback={null}>
          <KnowledgeGraphPage params={Promise.resolve({ id: "r1" })} />
        </Suspense>,
      );
    });
  };

  it("keeps the map for an absent ?view=", async () => {
    await mount();
    expect(screen.getByTestId("map-skeleton")).toBeTruthy();
    expect(screen.queryByTestId("c4-view")).toBeNull();
    expect(screen.getByRole("radio", { name: "Map" }).getAttribute("aria-checked")).toBe("true");
  });

  it("mounts the C4 diagram for ?view=c4, and not the map", async () => {
    await mount("c4");
    expect(screen.getByTestId("c4-view").textContent).toBe("c4:r1");
    expect(screen.queryByTestId("map-skeleton")).toBeNull();
    expect(screen.getByRole("radio", { name: "C4 diagram" }).getAttribute("aria-checked")).toBe("true");
    // The Structurizr export stays in the header on both views.
    expect(screen.getByRole("button", { name: "Structurizr DSL" })).toBeTruthy();
  });

  it("writes ?view=c4 from the switch and clears it back to the map", async () => {
    await mount();
    fireEvent.click(screen.getByRole("radio", { name: "C4 diagram" }));
    expect(mocks.setView).toHaveBeenLastCalledWith("c4");
    cleanup();
    await mount("c4");
    fireEvent.click(screen.getByRole("radio", { name: "Map" }));
    expect(mocks.setView).toHaveBeenLastCalledWith(null);
  });
});
