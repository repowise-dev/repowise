import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ArchitectureView } from "@repowise-dev/ui/c4";
import type { WebviewHost } from "../../runtime/rpc";
import { App } from "./App";

// React Flow observes canvas size and visibility; jsdom ships neither.
beforeAll(() => {
  class ObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): [] {
      return [];
    }
  }
  vi.stubGlobal("ResizeObserver", ObserverStub);
  vi.stubGlobal("IntersectionObserver", ObserverStub);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function makeNode(id: string, filePath: string | null): ArchitectureView["nodes"][number] {
  return {
    id,
    node_type: "file",
    name: id,
    file_path: filePath,
    line_range: null,
    summary: "",
    complexity: "simple",
    tags: [],
    language: "typescript",
    pagerank: 0,
    pagerank_percentile: 0,
    betweenness: 0,
    in_degree: 0,
    out_degree: 0,
    community_id: null,
    is_entry_point: false,
    is_test: false,
    is_hotspot: false,
    is_dead: false,
    has_doc: false,
    primary_owner: null,
    primary_owner_pct: null,
    bus_factor: null,
  };
}

const FIXTURE: ArchitectureView = {
  project_name: "demo",
  project_description: "A demo system.",
  layers: [
    {
      id: "layer:app",
      name: "Application",
      description: "App layer",
      node_ids: ["a", "b"],
      file_count: 2,
      complexity_distribution: { simple: 2 },
      health_score: 8,
      sub_groups: [],
      display_order: 0,
    },
  ],
  nodes: [makeNode("a", "src/a.ts"), makeNode("b", "src/b.ts")],
  edges: [{ source: "a", target: "b", edge_type: "import", direction: "forward", weight: 1, confidence: 1 }],
  tour: [],
  total_files: 2,
  total_symbols: 2,
  total_edges: 1,
  languages: ["typescript"],
  frameworks: [],
  external_systems: [],
  entry_points: [],
  entry_candidates: [],
};

function makeHost(overrides: Partial<WebviewHost["api"]> = {}): WebviewHost {
  const api = {
    architectureView: vi.fn().mockResolvedValue(FIXTURE),
    fileContent: vi.fn().mockResolvedValue(""),
    ...overrides,
  } as unknown as WebviewHost["api"];
  return {
    api,
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
  };
}

describe("architecture App", () => {
  it("renders the header and canvas shell from an architectureView fixture", async () => {
    const host = makeHost();
    render(
      <App
        host={host}
        repo={{ id: "r1", name: "acme/widgets", headCommit: null, defaultBranch: null }}
        params={{}}
        refreshToken={0}
      />,
    );

    expect(await screen.findByText("acme/widgets")).toBeDefined();
    expect(screen.getByTestId("architecture-shell")).toBeDefined();
    expect(screen.getByTestId("architecture-canvas")).toBeDefined();
    await waitFor(() => expect(host.api.architectureView).toHaveBeenCalledTimes(1));
  });

  it("surfaces a styled error when the RPC rejects", async () => {
    const host = makeHost({
      architectureView: vi.fn().mockRejectedValue(new Error("index unavailable")),
    });
    render(
      <App
        host={host}
        repo={{ id: "r1", name: "acme/widgets", headCommit: null, defaultBranch: null }}
        params={{}}
        refreshToken={0}
      />,
    );

    expect(await screen.findByText("index unavailable")).toBeDefined();
  });
});
