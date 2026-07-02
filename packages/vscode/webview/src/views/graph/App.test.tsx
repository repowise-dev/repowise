import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";

afterEach(cleanup);
import type { WebviewHost } from "../../runtime/rpc";
import { useGraphData } from "./use-graph-data";
import { GraphHeader } from "./graph-header";

/** A WebviewHost whose every api method is a vi.fn resolving to empty data, so a
 *  test can assert exactly which slices a scope requested. */
function mockHost(): { host: WebviewHost; api: Record<string, ReturnType<typeof vi.fn>> } {
  const api = {
    moduleGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    fullGraph: vi.fn().mockResolvedValue({ nodes: [], links: [] }),
    architectureCommunityGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    communities: vi.fn().mockResolvedValue([]),
    communitySlice: vi
      .fn()
      .mockResolvedValue({ nodes: [], links: [], community_id: 0, member_count: 0 }),
    deadCodeGraph: vi.fn().mockResolvedValue({ nodes: [], links: [] }),
    hotFilesGraph: vi.fn().mockResolvedValue({ nodes: [], links: [] }),
    executionFlows: vi.fn().mockResolvedValue({ total_entry_points: 0, flows: [] }),
  };
  const host = {
    api: api as unknown as WebviewHost["api"],
    onInit: () => () => {},
    onRefresh: () => () => {},
    onUpdateDone: () => () => {},
    onThemeChanged: () => () => {},
    ready: () => {},
    openFile: vi.fn(),
    copyText: vi.fn(),
    openExternal: vi.fn(),
    openView: vi.fn(),
    updateIndex: vi.fn(),
    setTheme: vi.fn(),
  } satisfies WebviewHost;
  return { host, api };
}

describe("useGraphData", () => {
  it("requests only the constellation scope on mount", () => {
    const { host, api } = mockHost();
    renderHook(() => useGraphData(host, 0));

    // Default scope = radial community constellation.
    expect(api.architectureCommunityGraph).toHaveBeenCalledTimes(1);
    expect(api.communities).toHaveBeenCalledTimes(1);

    // Nothing that a different scope renders is fetched upfront.
    expect(api.moduleGraph).not.toHaveBeenCalled();
    expect(api.fullGraph).not.toHaveBeenCalled();
    expect(api.deadCodeGraph).not.toHaveBeenCalled();
    expect(api.hotFilesGraph).not.toHaveBeenCalled();
    expect(api.executionFlows).not.toHaveBeenCalled();
    expect(api.communitySlice).not.toHaveBeenCalled();
  });

  it("fetches the file graph and flows only on switch to the full scope", () => {
    const { host, api } = mockHost();
    const { result } = renderHook(() => useGraphData(host, 0));

    expect(api.fullGraph).not.toHaveBeenCalled();

    act(() => result.current.setViewMode("full"));

    expect(api.fullGraph).toHaveBeenCalledTimes(1);
    expect(api.executionFlows).toHaveBeenCalledTimes(1);
    // The constellation graph is not refetched on the switch.
    expect(api.architectureCommunityGraph).toHaveBeenCalledTimes(1);
  });

  it("fetches a slice for each newly-expanded hub exactly once", () => {
    const { host, api } = mockHost();
    const { result } = renderHook(() => useGraphData(host, 0));

    act(() => result.current.setExpandedHubs([1, 2]));
    expect(api.communitySlice).toHaveBeenCalledTimes(2);

    // Re-expanding an already-loaded hub does not refetch it.
    act(() => result.current.setExpandedHubs([1, 2, 3]));
    expect(api.communitySlice).toHaveBeenCalledTimes(3);
  });
});

describe("GraphHeader", () => {
  it("renders the repo name and node/edge counts", () => {
    render(<GraphHeader repoName="acme/repo" stats={{ nodes: 2048, edges: 5120 }} />);
    expect(screen.getByText("Knowledge Graph")).toBeTruthy();
    expect(screen.getByText("acme/repo")).toBeTruthy();
    expect(screen.getByText("2,048")).toBeTruthy();
    expect(screen.getByText("5,120")).toBeTruthy();
  });
});
