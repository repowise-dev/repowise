import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ZoomNode } from "@repowise-dev/ui/zoom";
import type { WebviewHost } from "../../runtime/rpc";
import { App } from "./App";

afterEach(cleanup);

// The real canvas paints with Canvas 2D, which jsdom lacks. The stand-in
// reports a focus chain and lets a test select a node, which is all the
// panel wires.
const ROOT = { id: "root", parent_id: null, kind: "system", name: "repowise", path: "" };
const FILE = { id: "f1", parent_id: "root", kind: "file", name: "app.py", path: "src/app.py" };
const seenFocus: (string | undefined)[] = [];
vi.mock("@repowise-dev/ui/zoom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@repowise-dev/ui/zoom")>();
  const { forwardRef, useEffect } = await import("react");
  return {
    ...actual,
    ZoomCanvas: forwardRef(function FakeCanvas(
      props: {
        initialFocusId?: string;
        onFocusChange?: (c: ZoomNode[]) => void;
        onSelect?: (n: ZoomNode | null) => void;
      },
      _ref,
    ) {
      seenFocus.push(props.initialFocusId);
      useEffect(() => props.onFocusChange?.([ROOT as unknown as ZoomNode]), []);
      return (
        <button type="button" onClick={() => props.onSelect?.(FILE as unknown as ZoomNode)}>
          canvas
        </button>
      );
    }),
  };
});

function mockHost(zoomMap: Promise<unknown>): WebviewHost {
  return {
    api: { zoomMap: vi.fn(() => zoomMap) } as unknown as WebviewHost["api"],
    onInit: () => () => {},
    onRefresh: () => () => {},
    onUpdateDone: () => () => {},
    onThemeChanged: () => () => {},
    ready: () => {},
    openFile: vi.fn(),
    copyText: vi.fn(),
    openExternal: vi.fn(),
    openView: vi.fn(),
    focusHome: vi.fn(),
    openNativeSettings: vi.fn(),
    updateIndex: vi.fn(),
    setTheme: vi.fn(),
  };
}

const REPO = { id: "r", name: "repowise", headCommit: null, defaultBranch: "main" };
const MAP = { root_id: "root", nodes: [ROOT, FILE], relations: [], total_files: 1 };

describe("Knowledge Graph panel", () => {
  it("draws the zoom map with its breadcrumb and opens a selected file", async () => {
    const host = mockHost(Promise.resolve(MAP));
    render(<App host={host} repo={REPO} params={{}} refreshToken={0} />);

    expect(await screen.findByText("canvas")).toBeTruthy();
    expect(host.api.zoomMap).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("navigation", { name: "Zoom location" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "Map controls" })).toBeTruthy();

    screen.getByText("canvas").click();
    expect(host.openFile).toHaveBeenCalledWith("src/app.py");
  });

  it("resolves a repo path open parameter to the zoom node carrying it", async () => {
    seenFocus.length = 0;
    const host = mockHost(Promise.resolve(MAP));
    render(<App host={host} repo={REPO} params={{ selectNode: "src/app.py" }} refreshToken={0} />);

    await screen.findByText("canvas");
    expect(seenFocus.at(-1)).toBe("f1");
  });

  it("shows the error when the map cannot load", async () => {
    const host = mockHost(Promise.reject(new Error("no index")));
    render(<App host={host} repo={REPO} params={{}} refreshToken={0} />);

    expect(await screen.findByText("Could not load the knowledge graph.")).toBeTruthy();
    expect(screen.getByText("no index")).toBeTruthy();
  });
});
