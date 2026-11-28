import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { PRODUCTION_ONLY } from "@repowise-dev/types/graph";
import type { WebviewHost } from "../../runtime/rpc";
import {
  useArchitectureData,
  useHostQuery,
  type ArchitectureDataInput,
} from "./use-architecture-data";

afterEach(cleanup);

/** A WebviewHost whose every graph call is a vi.fn resolving to empty data, so
 *  a test can assert exactly which slices a scope requested. */
function mockHost(): { host: WebviewHost; api: Record<string, ReturnType<typeof vi.fn>> } {
  const api = {
    fullGraph: vi.fn().mockResolvedValue({ nodes: [], links: [] }),
    architectureCommunityGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    communities: vi.fn().mockResolvedValue([]),
    communitySlice: vi.fn().mockResolvedValue({ nodes: [], links: [] }),
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
    focusHome: vi.fn(),
    openNativeSettings: vi.fn(),
    updateIndex: vi.fn(),
    setTheme: vi.fn(),
  } satisfies WebviewHost;
  return { host, api };
}

const FILES: ArchitectureDataInput = {
  viewMode: "full",
  tree: false,
  population: PRODUCTION_ONLY,
  narrowingPopulation: undefined,
  activeCommunity: null,
  graphLimit: undefined,
  flowsRequested: false,
};

describe("useArchitectureData", () => {
  it("requests only the capped file graph and community labels on the Files scope", async () => {
    const { host, api } = mockHost();
    const { result } = renderHook(() => useArchitectureData(host, 0, FILES));

    await waitFor(() => expect(result.current.fullGraph.isLoading).toBe(false));
    expect(api.fullGraph).toHaveBeenCalledTimes(1);
    expect(api.communities).toHaveBeenCalled();
    expect(api.architectureCommunityGraph).not.toHaveBeenCalled();
    expect(api.deadCodeGraph).not.toHaveBeenCalled();
    expect(api.hotFilesGraph).not.toHaveBeenCalled();
    expect(api.communitySlice).not.toHaveBeenCalled();
    expect(api.executionFlows).not.toHaveBeenCalled();
  });

  it("fetches the constellation with the population on the Communities scope", async () => {
    const { host, api } = mockHost();
    const population = { tests: true, examples: false, docs: false };
    renderHook(() =>
      useArchitectureData(host, 0, { ...FILES, viewMode: "architecture", population }),
    );

    await waitFor(() => expect(api.architectureCommunityGraph).toHaveBeenCalledWith(population));
    expect(api.fullGraph).not.toHaveBeenCalled();
  });

  it("fetches an entered community's slice instead of the whole file graph", async () => {
    const { host, api } = mockHost();
    renderHook(() => useArchitectureData(host, 0, { ...FILES, activeCommunity: 3 }));

    await waitFor(() => expect(api.communitySlice).toHaveBeenCalledWith(3, PRODUCTION_ONLY));
  });

  it("passes the stepped node cap through and refetches when it changes", async () => {
    const { host, api } = mockHost();
    const { rerender } = renderHook((input: ArchitectureDataInput) =>
      useArchitectureData(host, 0, input), { initialProps: FILES });
    await waitFor(() => expect(api.fullGraph).toHaveBeenCalledWith(undefined));

    rerender({ ...FILES, graphLimit: 3000 });
    await waitFor(() => expect(api.fullGraph).toHaveBeenCalledWith(3000));
  });

  it("the outline reads only the file graph", async () => {
    const { host, api } = mockHost();
    renderHook(() => useArchitectureData(host, 0, { ...FILES, tree: true }));

    await waitFor(() => expect(api.fullGraph).toHaveBeenCalledTimes(1));
    expect(api.communities).not.toHaveBeenCalled();
  });

  it("surfaces a failed slice as a presentable error", async () => {
    const { host, api } = mockHost();
    api.fullGraph!.mockRejectedValueOnce(new Error("server down"));
    const { result } = renderHook(() => useArchitectureData(host, 0, FILES));

    await waitFor(() => expect(result.current.error).toBe("server down"));
  });
});

describe("useHostQuery", () => {
  it("does nothing for a null key and refetches when the index moves", async () => {
    const fetcher = vi.fn().mockResolvedValue(1);
    const { result, rerender } = renderHook(
      ({ k, token }: { k: string | null; token: number }) => useHostQuery(k, fetcher, token),
      { initialProps: { k: null as string | null, token: 0 } },
    );
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.isLoading).toBe(false);

    rerender({ k: "a", token: 0 });
    await waitFor(() => expect(result.current.data).toBe(1));

    rerender({ k: "a", token: 1 });
    // The last payload stays readable while the refetch is in flight.
    expect(result.current.data).toBe(1);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  });
});
