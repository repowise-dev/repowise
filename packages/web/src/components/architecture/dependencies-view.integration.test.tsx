// @vitest-environment jsdom

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExternalSystemsSummary } from "@repowise-dev/types/external-systems";

const mocks = vi.hoisted(() => ({
  setQueryStates: vi.fn(),
  setPackage: vi.fn(),
  setScope: vi.fn(),
  infiniteResult: {} as Record<string, unknown>,
  focusedResult: {} as Record<string, unknown>,
  summaryKey: "",
  queryValues: {} as Record<string, unknown>,
  swrCalls: [] as Array<{ key: string | null; fetcher: () => Promise<unknown> }>,
  autoFetchFiles: false,
  tableProps: {} as Record<string, unknown>,
  apiGet: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props}>{children}</a>
  ),
}));

vi.mock("nuqs", () => {
  const parser = (defaultValue: unknown = null) => ({
    defaultValue,
    withDefault(value: unknown) {
      return parser(value);
    },
  });
  return {
    parseAsInteger: parser(),
    parseAsString: parser(),
    parseAsStringLiteral: () => parser(),
    useQueryState: (name: string, value: { defaultValue?: unknown }) => {
      const current = name in mocks.queryValues ? mocks.queryValues[name] : value?.defaultValue ?? null;
      const setter = name === "package" ? mocks.setPackage : name === "scope" ? mocks.setScope : vi.fn();
      return [current, setter];
    },
    useQueryStates: (config: Record<string, { defaultValue?: unknown }>) => [
      Object.fromEntries(Object.entries(config).map(([key, value]) => [key, value.defaultValue])),
      mocks.setQueryStates,
    ],
  };
});

vi.mock("swr/infinite", () => ({
  default: (getKey: (index: number, previous: ExternalSystemsSummary | null) => string) => {
    mocks.summaryKey = getKey(0, null);
    return mocks.infiniteResult;
  },
}));

vi.mock("swr", () => ({
  default: (key: string | null, fetcher: () => Promise<unknown>) => {
    mocks.swrCalls.push({ key, fetcher });
    if (mocks.autoFetchFiles && key?.startsWith("external-system-files:")) void fetcher();
    return mocks.focusedResult;
  },
}));
vi.mock("@repowise-dev/api-client", () => ({ apiGet: mocks.apiGet }));
vi.mock("@/lib/api/client", () => ({}));

vi.mock("@repowise-dev/ui/dependencies", async () => {
  const actual = await vi.importActual<typeof import("@repowise-dev/ui/dependencies")>(
    "@repowise-dev/ui/dependencies",
  );
  return {
    ...actual,
    ExternalDependenciesTable: (props: {
      onStateChange: (state: typeof actual.DEFAULT_EXTERNAL_DEPENDENCY_STATE) => void;
      onSelectedChange: (entry: { package_key: string }) => void;
      onShowRelationships: () => void;
      onToggleAggregate: (key: string) => void;
    }) => (
      <div ref={() => { mocks.tableProps = props as unknown as Record<string, unknown>; }}>
        <button
          onClick={() => props.onStateChange({ ...actual.DEFAULT_EXTERNAL_DEPENDENCY_STATE, query: "react", page: 2 })}
        >
          Change package query
        </button>
        <button onClick={() => props.onSelectedChange({ package_key: "npm:react" })}>
          Select package
        </button>
        <button onClick={props.onShowRelationships}>Show relationships</button>
        <button onClick={() => props.onToggleAggregate("community:1")}>Expand area</button>
      </div>
    ),
  };
});

import { DependenciesView } from "./dependencies-view";

afterEach(cleanup);

const summary: ExternalSystemsSummary = {
  items: [
    {
      package_key: "npm:react",
      name: "react",
      display_name: "React",
      ecosystem: "npm",
      category: "framework",
      io_kind: null,
      runtime_declared: true,
      dev_declared: false,
      declaration_count: 1,
      manifest_count: 1,
      versions: ["19"],
      versions_total: 1,
      versions_truncated: false,
      multiple_versions: false,
      external_node_count: 1,
      import_edge_count: 2,
      importing_file_count: 2,
      link_state: "linked",
    },
    {
      package_key: "npm:next",
      name: "next",
      display_name: "Next",
      ecosystem: "npm",
      category: "framework",
      io_kind: null,
      runtime_declared: true,
      dev_declared: false,
      declaration_count: 1,
      manifest_count: 1,
      versions: ["15"],
      versions_total: 1,
      versions_truncated: false,
      multiple_versions: false,
      external_node_count: 1,
      import_edge_count: 1,
      importing_file_count: 1,
      link_state: "linked",
    },
  ],
  returned: 2,
  total_packages: 2,
  limit: 400,
  offset: 0,
  truncated: false,
  scope: "primary",
  excluded_declarations: 0,
  total_declarations: 2,
  runtime_packages: 2,
  dev_only_packages: 0,
  observed_packages: 2,
  linked_packages: 2,
  unlinked_packages: 0,
  linked_without_imports: 0,
  ecosystems: ["npm"],
  manifest_count: 1,
};

describe("DependenciesView wiring", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.queryValues = {};
    mocks.swrCalls = [];
    mocks.autoFetchFiles = false;
    mocks.tableProps = {};
    mocks.apiGet.mockResolvedValue({});
    mocks.infiniteResult = {
      data: [summary],
      error: undefined,
      isLoading: false,
      isValidating: false,
      mutate: vi.fn(),
      size: 1,
      setSize: vi.fn(),
    };
    mocks.focusedResult = { data: undefined, error: undefined, isLoading: false };
  });

  it("starts from the bounded summary key and does not activate a graph request", () => {
    render(<DependenciesView repoId="repo-id" />);
    expect(mocks.summaryKey).toBe("external-systems-summary:repo-id:primary:0");
    expect(mocks.swrCalls.filter((call) => call.key !== null)).toHaveLength(0);
    expect(mocks.apiGet).not.toHaveBeenCalled();
  });

  it("renders loading and error states", () => {
    mocks.infiniteResult = { ...mocks.infiniteResult, data: undefined, isLoading: true };
    const { rerender } = render(<DependenciesView repoId="repo-id" />);
    expect(screen.getByLabelText("Loading external dependencies")).toBeTruthy();

    mocks.infiniteResult = { ...mocks.infiniteResult, isLoading: false, error: new Error("offline") };
    rerender(<DependenciesView repoId="repo-id" />);
    expect(screen.getByText("Couldn't load external dependencies")).toBeTruthy();
  });

  it("writes controlled filter and package selection state back to the URL layer", () => {
    render(<DependenciesView repoId="repo-id" />);
    fireEvent.click(screen.getByRole("button", { name: "Change package query" }));
    expect(mocks.setQueryStates).toHaveBeenCalledWith(expect.objectContaining({ q: "react", page: 2 }));

    fireEvent.click(screen.getByRole("button", { name: "Select package" }));
    expect(mocks.setPackage).toHaveBeenCalledWith("npm:react");
  });

  it("restores URL-backed package focus and issues one focused request after interaction", async () => {
    mocks.queryValues = { package: "npm:react", focus: "relationships" };
    render(<DependenciesView repoId="repo-id" />);

    expect(mocks.tableProps.selected).toEqual(expect.objectContaining({ package_key: "npm:react" }));
    expect(mocks.tableProps.relationshipsOpen).toBe(true);
    const active = mocks.swrCalls.filter((call) => call.key?.startsWith("external-system-relationships:"));
    expect(active).toHaveLength(1);
    await active[0]!.fetcher();
    expect(mocks.apiGet).toHaveBeenCalledTimes(1);
    expect(mocks.apiGet.mock.calls[0]![0]).toContain("/external-systems/npm%3Areact/graph");
    expect(mocks.apiGet.mock.calls[0]![0]).not.toContain("/api/graph/");
  });

  it("cancels a stale focused request when package identity changes", () => {
    mocks.queryValues = { package: "npm:react", focus: "relationships" };
    const { rerender } = render(<DependenciesView repoId="repo-id" />);
    const firstFetcher = mocks.swrCalls.find((call) => call.key?.includes("npm:react"))!.fetcher;
    void firstFetcher();
    const firstSignal = (mocks.apiGet.mock.calls[0]![2] as { signal: AbortSignal }).signal;
    expect(firstSignal.aborted).toBe(false);

    mocks.queryValues = { package: "npm:next", focus: "relationships" };
    mocks.swrCalls = [];
    rerender(<DependenciesView repoId="repo-id" />);
    expect(firstSignal.aborted).toBe(true);
    expect(mocks.swrCalls.some((call) => call.key?.includes("npm:next"))).toBe(true);
  });

  it("keeps importing-file requests independently bounded", async () => {
    mocks.queryValues = {
      package: "npm:react",
      focus: "relationships",
      area: "community:1",
      fileOffset: 25,
    };
    render(<DependenciesView repoId="repo-id" />);
    const files = mocks.swrCalls.find((call) => call.key?.startsWith("external-system-files:"));
    expect(files).toBeDefined();
    await files!.fetcher();
    expect(mocks.apiGet).toHaveBeenCalledWith(
      expect.stringContaining("/graph/files"),
      expect.objectContaining({ aggregate_key: "community:1", limit: 25, offset: 25 }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("does not cancel a newly started file request when an area is expanded", () => {
    mocks.queryValues = { package: "npm:react", focus: "relationships" };
    const { rerender } = render(<DependenciesView repoId="repo-id" />);

    mocks.autoFetchFiles = true;
    mocks.queryValues = {
      package: "npm:react",
      focus: "relationships",
      area: "community:1",
    };
    rerender(<DependenciesView repoId="repo-id" />);

    const fileCall = mocks.apiGet.mock.calls.find(([path]) => String(path).includes("/graph/files"));
    expect(fileCall).toBeDefined();
    const signal = (fileCall![2] as { signal: AbortSignal }).signal;
    expect(signal.aborted).toBe(false);
  });
});
