// @vitest-environment jsdom

/**
 * The Architecture page's routing of the tree scope.
 *
 * `?view=tree` has to land on the outline and nothing else: not on the canvas,
 * not on a table tab. The page resolves `?view=` through an alias table and a
 * legacy `?viewMode=` translation before it picks a panel, so a regression that
 * adds the view to `CANONICAL` but forgets the panel (or the reverse) renders
 * the wrong surface under a correct-looking URL. That is what this pins.
 *
 * The two surfaces and the tab row are stubbed: this is about which one the
 * page mounts, not about what any of them renders.
 */

import * as React from "react";
import { Suspense } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  queryValues: {} as Record<string, unknown>,
  setView: vi.fn(),
}));

vi.mock("nuqs", () => {
  const parser = (defaultValue: unknown = null) => ({
    defaultValue,
    withDefault(value: unknown) {
      return parser(value);
    },
    withOptions() {
      return parser();
    },
  });
  return {
    parseAsString: parser(),
    parseAsStringLiteral: () => parser(),
    parseAsInteger: parser(),
    useQueryState: (name: string, value: { defaultValue?: unknown }) => [
      name in mocks.queryValues ? mocks.queryValues[name] : value?.defaultValue ?? null,
      name === "view" ? mocks.setView : vi.fn(),
    ],
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

vi.mock("@/components/architecture/graph-view", () => ({
  GraphView: (props: { scope: string }) => (
    <div data-testid="graph-view">canvas:{props.scope}</div>
  ),
}));

vi.mock("@/components/architecture/code-graph-tree-view", () => ({
  CodeGraphTreeView: (props: { scope: string }) => (
    <div data-testid="tree-view">tree:{props.scope}</div>
  ),
}));

vi.mock("@/components/architecture/dependencies-view", () => ({
  DependenciesView: () => <div data-testid="dependencies-view" />,
}));

vi.mock("@/components/coupling/coupling-tab", () => ({
  CouplingTab: () => <div data-testid="coupling-tab" />,
}));

vi.mock("@/components/symbols/symbol-table-wrapper", () => ({
  SymbolTableWrapper: () => <div data-testid="symbol-table" />,
}));

import ArchitecturePage from "./page";

afterEach(cleanup);

// jsdom does not implement scrollIntoView, and the shared ViewTabs calls it on
// the active tab to keep it in view. A stub here rather than in the component:
// the component is right, the environment is the one missing the method.
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

describe("Architecture page view routing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.queryValues = {};
  });

  // `use(params)` suspends the first render while the route promise resolves,
  // so every mount is wrapped in a boundary and awaited before assertions.
  const mount = async (view?: string) => {
    mocks.queryValues = view === undefined ? {} : { view };
    await act(async () => {
      render(
        <Suspense fallback={<div data-testid="suspended" />}>
          <ArchitecturePage params={Promise.resolve({ id: "r1" })} />
        </Suspense>,
      );
    });
  };

  it("mounts the tree for ?view=tree, and not the canvas", async () => {
    await mount("tree");
    expect(screen.getByTestId("tree-view").textContent).toBe("tree:tree");
    expect(screen.queryByTestId("graph-view")).toBeNull();
  });

  it("still mounts the canvas for the two scopes it draws", async () => {
    await mount("files");
    expect(screen.getByTestId("graph-view").textContent).toBe("canvas:files");
    cleanup();
    await mount("communities");
    expect(screen.getByTestId("graph-view").textContent).toBe("canvas:communities");
    expect(screen.queryByTestId("tree-view")).toBeNull();
  });

  it("keeps the tree off every non-Map tab", async () => {
    for (const view of ["coupling", "packages", "symbols"]) {
      cleanup();
      await mount(view);
      expect(screen.queryByTestId("tree-view")).toBeNull();
      expect(screen.queryByTestId("graph-view")).toBeNull();
    }
  });

  it("lands an absent ?view= on the canvas, not on the tree", async () => {
    await mount();
    expect(screen.getByTestId("graph-view").textContent).toBe("canvas:communities");
    expect(screen.queryByTestId("tree-view")).toBeNull();
  });
});
