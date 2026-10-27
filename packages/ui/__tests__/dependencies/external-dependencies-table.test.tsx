import * as React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  ExternalSystemImportingFiles,
  ExternalSystemRelationshipGraph,
  ExternalSystemSummaryEntry,
  ExternalSystemsSummary,
} from "@repowise-dev/types/external-systems";
import {
  DEFAULT_EXTERNAL_DEPENDENCY_STATE,
  ExternalDependenciesTable,
  PackageRelationshipGraph,
  type ExternalDependencyTableState,
} from "../../src/dependencies";

function entry(index: number, overrides: Partial<ExternalSystemSummaryEntry> = {}): ExternalSystemSummaryEntry {
  return {
    package_key: `npm:pkg-${index}`,
    name: `pkg-${index}`,
    display_name: `pkg-${index}`,
    ecosystem: index % 2 ? "pypi" : "npm",
    category: "library",
    io_kind: null,
    runtime_declared: index % 3 !== 0,
    dev_declared: index % 3 === 0,
    declaration_count: 1,
    manifest_count: 1,
    versions: [`^${index}.0.0`],
    versions_total: 1,
    versions_truncated: false,
    multiple_versions: false,
    external_node_count: index % 4 === 0 ? 0 : 1,
    import_edge_count: index,
    importing_file_count: index,
    link_state: index % 4 === 0 ? "unlinked" : "linked",
    ...overrides,
  };
}

function relationshipGraph(
  overrides: Partial<ExternalSystemRelationshipGraph> = {},
): ExternalSystemRelationshipGraph {
  return {
    package_key: "npm:react",
    package_name: "react",
    package_node_id: "package:npm:react",
    match_basis: "mixed",
    matched_external_nodes: [
      { node_id: "external:react", match_basis: "exact" },
      { node_id: "external:react/jsx-runtime", match_basis: "subpath" },
    ],
    matched_external_nodes_total: 2,
    matched_external_nodes_truncated: false,
    evidence_target_limit: 200,
    evidence_truncated: false,
    nodes: [
      {
        aggregate_key: "community:1",
        label: "Web runtime",
        community_id: 1,
        importing_file_count: 2,
        import_edge_count: 3,
        top_file: "src/app.tsx",
      },
    ],
    edges: [{ source: "community:1", target: "package:npm:react", import_edge_count: 3 }],
    aggregate_total: 1,
    aggregate_returned: 1,
    edge_total: 1,
    edge_returned: 1,
    importing_file_total: 2,
    import_edge_total: 3,
    node_limit: 50,
    edge_limit: 200,
    truncated: false,
    scope: "primary",
    ...overrides,
  };
}

const importingFiles: ExternalSystemImportingFiles = {
  package_key: "npm:react",
  aggregate_key: "community:1",
  items: [
    {
      path: "src/app.tsx",
      language: "typescript",
      import_edge_count: 2,
      matched_external_node_count: 2,
    },
  ],
  total: 2,
  returned: 1,
  limit: 1,
  offset: 0,
  truncated: true,
  scope: "primary",
};

function summary(items: ExternalSystemSummaryEntry[]): ExternalSystemsSummary {
  return {
    items,
    returned: items.length,
    total_packages: items.length,
    limit: 400,
    offset: 0,
    truncated: false,
    scope: "primary",
    excluded_declarations: 7,
    total_declarations: items.length,
    runtime_packages: items.filter((item) => item.runtime_declared).length,
    dev_only_packages: items.filter((item) => !item.runtime_declared && item.dev_declared).length,
    observed_packages: items.filter((item) => item.import_edge_count > 0).length,
    linked_packages: items.filter((item) => item.link_state === "linked").length,
    unlinked_packages: items.filter((item) => item.link_state === "unlinked").length,
    linked_without_imports: items.filter((item) => item.link_state === "linked" && item.import_edge_count === 0).length,
    ecosystems: [...new Set(items.map((item) => item.ecosystem))],
    manifest_count: 3,
  };
}

function Harness({ data }: { data: ExternalSystemsSummary }) {
  const [state, setState] = React.useState<ExternalDependencyTableState>({
    ...DEFAULT_EXTERNAL_DEPENDENCY_STATE,
    sort: "name",
    order: "asc",
  });
  const [selected, setSelected] = React.useState<ExternalSystemSummaryEntry | null>(null);
  return (
    <ExternalDependenciesTable
      data={data}
      state={state}
      onStateChange={setState}
      selected={selected}
      onSelectedChange={setSelected}
    />
  );
}

describe("ExternalDependenciesTable", () => {
  it("keeps the rendered package page bounded and paginates", () => {
    render(<Harness data={summary(Array.from({ length: 60 }, (_, index) => entry(index)))} />);

    expect(screen.getAllByText("pkg-0").length).toBeGreaterThan(0);
    expect(screen.queryByText("pkg-31")).not.toBeInTheDocument();
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(screen.getAllByText("pkg-31").length).toBeGreaterThan(0);
    expect(screen.queryByText("pkg-0")).not.toBeInTheDocument();
  });

  it("searches quickly and reports an honest filtered empty state", () => {
    render(<Harness data={summary([entry(1), entry(2), entry(3)])} />);
    const search = screen.getByRole("searchbox", { name: "Search packages" });

    fireEvent.change(search, { target: { value: "pkg-2" } });
    expect(screen.getAllByText("pkg-2").length).toBeGreaterThan(0);
    expect(screen.queryByText("pkg-1")).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: "missing" } });
    expect(screen.getByText("No packages match these filters")).toBeInTheDocument();
  });

  it("opens a keyboard-accessible package inspector", () => {
    render(<Harness data={summary([entry(7, { display_name: "selected-package" })])} />);
    const row = screen.getAllByText("selected-package")[0]!.closest("tr");
    expect(row).not.toBeNull();
    fireEvent.keyDown(row!, { key: "Enter" });

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("selected-package")).toBeInTheDocument();
    expect(within(dialog).getByText(/1 declaration/)).toBeInTheDocument();
  });

  it("opens relationships from the inspector and exposes loading and error states", () => {
    const onShowRelationships = vi.fn();
    const selected = entry(7, { display_name: "React" });
    const { rerender } = render(
      <ExternalDependenciesTable
        data={summary([selected])}
        state={DEFAULT_EXTERNAL_DEPENDENCY_STATE}
        onStateChange={vi.fn()}
        selected={selected}
        onSelectedChange={vi.fn()}
        onShowRelationships={onShowRelationships}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show relationships" }));
    expect(onShowRelationships).toHaveBeenCalledOnce();

    rerender(
      <ExternalDependenciesTable
        data={summary([selected])}
        state={DEFAULT_EXTERNAL_DEPENDENCY_STATE}
        onStateChange={vi.fn()}
        selected={selected}
        onSelectedChange={vi.fn()}
        relationshipsOpen
        relationshipsLoading
        onHideRelationships={vi.fn()}
      />,
    );
    expect(screen.getByText("Loading package relationships…")).toBeInTheDocument();

    rerender(
      <ExternalDependenciesTable
        data={summary([selected])}
        state={DEFAULT_EXTERNAL_DEPENDENCY_STATE}
        onStateChange={vi.fn()}
        selected={selected}
        onSelectedChange={vi.fn()}
        relationshipsOpen
        relationshipsError="Relationship request failed"
        onHideRelationships={vi.fn()}
        onRetryRelationships={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Relationship request failed");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("renders empty and capped relationship evidence honestly", () => {
    const { rerender } = render(
      <PackageRelationshipGraph
        packageLabel="unlinked"
        graph={relationshipGraph({
          match_basis: "unresolved",
          matched_external_nodes: [],
          matched_external_nodes_total: 0,
          nodes: [],
          edges: [],
          aggregate_total: 0,
          aggregate_returned: 0,
          edge_total: 0,
          edge_returned: 0,
          importing_file_total: 0,
          import_edge_total: 0,
        })}
        onBack={vi.fn()}
        onToggleAggregate={vi.fn()}
        onFilesPageChange={vi.fn()}
      />,
    );
    expect(screen.getByText("No relationship evidence")).toBeInTheDocument();
    expect(screen.getByText(/not linked to a persisted external graph target/)).toBeInTheDocument();

    rerender(
      <PackageRelationshipGraph
        packageLabel="React"
        graph={relationshipGraph({ aggregate_total: 74, edge_total: 74, truncated: true })}
        onBack={vi.fn()}
        onToggleAggregate={vi.fn()}
        onFilesPageChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Showing 1 of 74 areas");
    expect(screen.getByRole("status")).toHaveTextContent("50 areas and 200 edges");
  });

  it("keeps aggregate expansion keyboard-accessible and file pages bounded", () => {
    const onToggleAggregate = vi.fn();
    const onFilesPageChange = vi.fn();
    render(
      <PackageRelationshipGraph
        packageLabel="React"
        graph={relationshipGraph()}
        expandedAggregateKey="community:1"
        files={importingFiles}
        onBack={vi.fn()}
        onToggleAggregate={onToggleAggregate}
        onFilesPageChange={onFilesPageChange}
      />,
    );
    const area = screen.getByRole("button", { name: /Web runtime/ });
    expect(area.tagName).toBe("BUTTON");
    expect(area).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(area);
    expect(onToggleAggregate).toHaveBeenCalledWith(null);
    expect(screen.getAllByText("src/app.tsx")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Next importing files" }));
    expect(onFilesPageChange).toHaveBeenCalledWith(1);
  });

  it("renders a repository-level empty state", () => {
    const onStateChange = vi.fn();
    render(
      <ExternalDependenciesTable
        data={summary([])}
        state={DEFAULT_EXTERNAL_DEPENDENCY_STATE}
        onStateChange={onStateChange}
        selected={null}
        onSelectedChange={vi.fn()}
      />,
    );
    expect(screen.getByText("No external dependencies recorded")).toBeInTheDocument();
  });

  it("offers bounded server-page traversal when more summaries exist", () => {
    const onLoadMore = vi.fn();
    const data = { ...summary([entry(1)]), total_packages: 501, returned: 400, truncated: true };
    render(
      <ExternalDependenciesTable
        data={data}
        state={DEFAULT_EXTERNAL_DEPENDENCY_STATE}
        onStateChange={vi.fn()}
        selected={null}
        onSelectedChange={vi.fn()}
        onLoadMore={onLoadMore}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Load next 101" }));
    expect(onLoadMore).toHaveBeenCalledOnce();
    expect(screen.getByText(/apply to 400 loaded packages out of 501/)).toBeInTheDocument();
  });
});
