"use client";

import * as React from "react";
import {
  ArrowRight,
  Box,
  ChevronLeft,
  ChevronRight,
  Network,
  Search,
} from "lucide-react";
import type {
  ExternalSystemImportingFiles,
  ExternalSystemRelationshipGraph,
  ExternalSystemSummaryEntry,
  ExternalSystemsSummary,
} from "@repowise-dev/types/external-systems";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import { Button } from "../ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { Sheet, SheetContent, SheetTitle } from "../ui/sheet";
import { PackageRelationshipGraph } from "./package-relationship-graph";

export type ExternalDependencyRole = "all" | "runtime" | "dev-only" | "mixed";
export type ExternalDependencyUsage =
  | "all"
  | "observed"
  | "linked-unobserved"
  | "unlinked";
export type ExternalDependencySort =
  | "importers"
  | "edges"
  | "name"
  | "declarations"
  | "manifests"
  | "versions";

export interface ExternalDependencyTableState {
  query: string;
  ecosystem: string;
  role: ExternalDependencyRole;
  usage: ExternalDependencyUsage;
  category: string;
  sort: ExternalDependencySort;
  order: "asc" | "desc";
  page: number;
}

export const DEFAULT_EXTERNAL_DEPENDENCY_STATE: ExternalDependencyTableState = {
  query: "",
  ecosystem: "all",
  role: "all",
  usage: "all",
  category: "all",
  sort: "importers",
  order: "desc",
  page: 1,
};

export interface ExternalDependenciesTableProps {
  data: ExternalSystemsSummary;
  state: ExternalDependencyTableState;
  onStateChange: (state: ExternalDependencyTableState) => void;
  selected: ExternalSystemSummaryEntry | null;
  onSelectedChange: (entry: ExternalSystemSummaryEntry | null) => void;
  relationshipsOpen?: boolean | undefined;
  relationships?: ExternalSystemRelationshipGraph | undefined;
  relationshipsLoading?: boolean | undefined;
  relationshipsError?: string | null | undefined;
  expandedAggregateKey?: string | null | undefined;
  importingFiles?: ExternalSystemImportingFiles | undefined;
  importingFilesLoading?: boolean | undefined;
  importingFilesError?: string | null | undefined;
  onShowRelationships?: (() => void) | undefined;
  onHideRelationships?: (() => void) | undefined;
  onRetryRelationships?: (() => void) | undefined;
  onToggleAggregate?: ((aggregateKey: string | null) => void) | undefined;
  onFilesPageChange?: ((offset: number) => void) | undefined;
  onRetryImportingFiles?: (() => void) | undefined;
  renderFileLink?: ((path: string, children: React.ReactNode) => React.ReactNode) | undefined;
  pageSize?: number | undefined;
  onLoadMore?: (() => void) | undefined;
  loadingMore?: boolean | undefined;
  loadMoreError?: string | null | undefined;
  onRetryLoadMore?: (() => void) | undefined;
}

function roleOf(entry: ExternalSystemSummaryEntry): Exclude<ExternalDependencyRole, "all"> {
  if (entry.runtime_declared && entry.dev_declared) return "mixed";
  return entry.runtime_declared ? "runtime" : "dev-only";
}

function usageOf(entry: ExternalSystemSummaryEntry): Exclude<ExternalDependencyUsage, "all"> {
  if (entry.import_edge_count > 0) return "observed";
  return entry.link_state === "linked" ? "linked-unobserved" : "unlinked";
}

function compareEntries(
  a: ExternalSystemSummaryEntry,
  b: ExternalSystemSummaryEntry,
  sort: ExternalDependencySort,
): number {
  if (sort === "name") return a.display_name.localeCompare(b.display_name);
  const values: Record<Exclude<ExternalDependencySort, "name">, [number, number]> = {
    importers: [a.importing_file_count, b.importing_file_count],
    edges: [a.import_edge_count, b.import_edge_count],
    declarations: [a.declaration_count, b.declaration_count],
    manifests: [a.manifest_count, b.manifest_count],
    versions: [a.versions_total, b.versions_total],
  };
  const [av, bv] = values[sort];
  return av - bv || a.display_name.localeCompare(b.display_name);
}

function updateState(
  state: ExternalDependencyTableState,
  patch: Partial<ExternalDependencyTableState>,
): ExternalDependencyTableState {
  return { ...state, ...patch, page: patch.page ?? 1 };
}

function RoleLabel({ entry }: { entry: ExternalSystemSummaryEntry }) {
  const role = roleOf(entry);
  return (
    <span className="text-xs text-[var(--color-text-secondary)]">
      {role === "runtime" ? "Runtime" : role === "mixed" ? "Runtime + dev" : "Dev-only"}
    </span>
  );
}

function LinkageLabel({ entry }: { entry: ExternalSystemSummaryEntry }) {
  const usage = usageOf(entry);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
      <span
        aria-hidden
        className={
          usage === "observed"
            ? "h-1.5 w-1.5 rounded-full bg-[var(--color-success)]"
            : "h-1.5 w-1.5 rounded-full bg-[var(--color-text-tertiary)]"
        }
      />
      {usage === "observed"
        ? "Observed"
        : usage === "linked-unobserved"
          ? "Linked, no imports"
          : "No graph link"}
    </span>
  );
}

function Versions({ entry }: { entry: ExternalSystemSummaryEntry }) {
  if (entry.versions_total === 0) {
    return <span className="text-[var(--color-text-tertiary)]">Not specified</span>;
  }
  const label = entry.versions.join(", ");
  return (
    <span className="font-mono text-xs text-[var(--color-text-secondary)]" title={label}>
      {label}
      {entry.versions_truncated ? ` +${entry.versions_total - entry.versions.length}` : ""}
      {entry.multiple_versions ? (
        <span className="ml-1.5 font-sans text-[var(--color-text-tertiary)]">
          ({entry.versions_total} versions)
        </span>
      ) : null}
    </span>
  );
}

function PackageInspector({
  entry,
  relationshipsOpen,
  relationships,
  relationshipsLoading,
  relationshipsError,
  expandedAggregateKey,
  importingFiles,
  importingFilesLoading,
  importingFilesError,
  onShowRelationships,
  onHideRelationships,
  onRetryRelationships,
  onToggleAggregate,
  onFilesPageChange,
  onRetryImportingFiles,
  renderFileLink,
  onClose,
}: {
  entry: ExternalSystemSummaryEntry | null;
  relationshipsOpen?: boolean | undefined;
  relationships?: ExternalSystemRelationshipGraph | undefined;
  relationshipsLoading?: boolean | undefined;
  relationshipsError?: string | null | undefined;
  expandedAggregateKey?: string | null | undefined;
  importingFiles?: ExternalSystemImportingFiles | undefined;
  importingFilesLoading?: boolean | undefined;
  importingFilesError?: string | null | undefined;
  onShowRelationships?: (() => void) | undefined;
  onHideRelationships?: (() => void) | undefined;
  onRetryRelationships?: (() => void) | undefined;
  onToggleAggregate?: ((aggregateKey: string | null) => void) | undefined;
  onFilesPageChange?: ((offset: number) => void) | undefined;
  onRetryImportingFiles?: (() => void) | undefined;
  renderFileLink?: ExternalDependenciesTableProps["renderFileLink"];
  onClose: () => void;
}) {
  return (
    <Sheet open={entry !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side="right"
        closeLabel="Close package details"
        className="w-[min(92vw,440px)] sm:w-[440px]"
      >
        {entry ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <header className="border-b border-[var(--color-border-default)] px-5 py-4 pr-12">
              <p className="font-mono text-2xs uppercase tracking-wider text-[var(--color-text-tertiary)]">
                {entry.ecosystem} package
              </p>
              <SheetTitle className="mt-1 break-words text-[15px]">{entry.display_name}</SheetTitle>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                <RoleLabel entry={entry} />
                <LinkageLabel entry={entry} />
              </div>
            </header>

            <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-5">
              {relationshipsOpen ? (
                relationshipsLoading && !relationships ? (
                  <div className="space-y-3" role="status">
                    <Button variant="ghost" size="sm" onClick={onHideRelationships}>Back to package details</Button>
                    <p className="text-xs text-[var(--color-text-tertiary)]">Loading package relationships…</p>
                  </div>
                ) : relationshipsError ? (
                  <div className="space-y-3" role="alert">
                    <Button variant="ghost" size="sm" onClick={onHideRelationships}>Back to package details</Button>
                    <p className="text-xs text-[var(--color-text-secondary)]">{relationshipsError}</p>
                    {onRetryRelationships ? <Button variant="outline" size="sm" onClick={onRetryRelationships}>Retry</Button> : null}
                  </div>
                ) : relationships ? (
                  <PackageRelationshipGraph
                    packageLabel={entry.display_name}
                    graph={relationships}
                    expandedAggregateKey={expandedAggregateKey}
                    files={importingFiles}
                    filesLoading={importingFilesLoading}
                    filesError={importingFilesError}
                    renderFileLink={renderFileLink}
                    onBack={() => onHideRelationships?.()}
                    onToggleAggregate={(key) => onToggleAggregate?.(key)}
                    onFilesPageChange={(offset) => onFilesPageChange?.(offset)}
                    onRetryFiles={onRetryImportingFiles}
                  />
                ) : null
              ) : (
                <>
              <section>
                <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Declarations</h3>
                <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
                  {entry.declaration_count} declaration{entry.declaration_count === 1 ? "" : "s"} across {entry.manifest_count} manifest{entry.manifest_count === 1 ? "" : "s"}.
                </p>
                <div className="mt-2"><Versions entry={entry} /></div>
                {entry.versions_truncated ? (
                  <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                    Showing {entry.versions.length} of {entry.versions_total} declared versions.
                  </p>
                ) : null}
              </section>

              <section>
                <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Import graph evidence</h3>
                <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
                  {entry.importing_file_count} importing file{entry.importing_file_count === 1 ? "" : "s"} and {entry.import_edge_count} import edge{entry.import_edge_count === 1 ? "" : "s"} in the persisted graph.
                </p>
                {entry.external_node_count > 1 ? (
                  <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">Evidence spans {entry.external_node_count} linked graph targets.</p>
                ) : entry.link_state === "unlinked" ? (
                  <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">This declaration has no linked external graph target.</p>
                ) : null}
              </section>
                </>
              )}
            </div>

            {!relationshipsOpen && onShowRelationships ? (
              <footer className="border-t border-[var(--color-border-default)] p-4">
                <Button className="w-full" onClick={onShowRelationships}>
                  <Network className="h-4 w-4" />
                  Show relationships
                </Button>
              </footer>
            ) : null}
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

export function ExternalDependenciesTable({
  data,
  state,
  onStateChange,
  selected,
  onSelectedChange,
  relationshipsOpen,
  relationships,
  relationshipsLoading,
  relationshipsError,
  expandedAggregateKey,
  importingFiles,
  importingFilesLoading,
  importingFilesError,
  onShowRelationships,
  onHideRelationships,
  onRetryRelationships,
  onToggleAggregate,
  onFilesPageChange,
  onRetryImportingFiles,
  renderFileLink,
  pageSize = 25,
  onLoadMore,
  loadingMore,
  loadMoreError,
  onRetryLoadMore,
}: ExternalDependenciesTableProps) {
  const deferredQuery = React.useDeferredValue(state.query.trim().toLowerCase());
  const categories = React.useMemo(
    () => [...new Set(data.items.map((entry) => entry.category))].sort(),
    [data.items],
  );

  const filtered = React.useMemo(() => {
    const rows = data.items.filter((entry) => {
      if (
        deferredQuery &&
        !entry.name.toLowerCase().includes(deferredQuery) &&
        !entry.display_name.toLowerCase().includes(deferredQuery) &&
        !entry.versions.some((version) => version.toLowerCase().includes(deferredQuery))
      )
        return false;
      if (state.ecosystem !== "all" && entry.ecosystem !== state.ecosystem) return false;
      if (state.role !== "all" && roleOf(entry) !== state.role) return false;
      if (state.usage !== "all" && usageOf(entry) !== state.usage) return false;
      if (state.category !== "all" && entry.category !== state.category) return false;
      return true;
    });
    return rows.sort((a, b) => {
      const result = compareEntries(a, b, state.sort);
      return state.order === "asc" ? result : -result;
    });
  }, [data.items, deferredQuery, state]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const page = Math.min(Math.max(1, state.page), pageCount);
  const pageRows = filtered.slice((page - 1) * pageSize, page * pageSize);
  const activeFilters =
    Number(Boolean(state.query)) +
    Number(state.ecosystem !== "all") +
    Number(state.role !== "all") +
    Number(state.usage !== "all") +
    Number(state.category !== "all");

  const handleSort = (key: string) => {
    const sort = key as ExternalDependencySort;
    onStateChange(
      updateState(state, {
        sort,
        order: state.sort === sort ? (state.order === "asc" ? "desc" : "asc") : sort === "name" ? "asc" : "desc",
      }),
    );
  };

  const columns: ResponsiveColumn<ExternalSystemSummaryEntry>[] = [
    {
      key: "name",
      header: "Package",
      priority: 1,
      sortable: true,
      cellClassName: "min-w-[220px]",
      render: (entry) => (
        <div className="min-w-0">
          <div className="flex items-start gap-2">
            <Box className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />
            <div className="min-w-0">
              <p className="break-words font-mono text-sm font-medium text-[var(--color-text-primary)]">
                {entry.display_name}
              </p>
              <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
                {entry.ecosystem} · {entry.category}
              </p>
            </div>
          </div>
        </div>
      ),
    },
    {
      key: "versions",
      header: "Versions",
      priority: 2,
      sortable: true,
      cellClassName: "min-w-[170px] max-w-[300px]",
      render: (entry) => <Versions entry={entry} />,
    },
    {
      key: "declarations",
      header: "Declarations",
      mobileLabel: "Declared",
      priority: 2,
      align: "right",
      sortable: true,
      render: (entry) => (
        <span className="font-mono text-xs tabular-nums">
          {entry.declaration_count} / {entry.manifest_count} manifest{entry.manifest_count === 1 ? "" : "s"}
        </span>
      ),
    },
    {
      key: "role",
      header: "Role",
      priority: 2,
      render: (entry) => <RoleLabel entry={entry} />,
    },
    {
      key: "importers",
      header: "Files",
      mobileLabel: "Importing files",
      priority: 1,
      align: "right",
      sortable: true,
      render: (entry) => <span className="font-mono tabular-nums">{entry.importing_file_count}</span>,
    },
    {
      key: "edges",
      header: "Edges",
      mobileLabel: "Import edges",
      priority: 3,
      align: "right",
      sortable: true,
      render: (entry) => <span className="font-mono tabular-nums">{entry.import_edge_count}</span>,
    },
    {
      key: "linkage",
      header: "Graph linkage",
      priority: 1,
      render: (entry) => <LinkageLabel entry={entry} />,
    },
    {
      key: "open",
      header: <span className="sr-only">Open</span>,
      priority: 3,
      align: "right",
      hideInCard: true,
      render: () => <ArrowRight className="ml-auto h-4 w-4 text-[var(--color-text-tertiary)]" aria-hidden />,
    },
  ];

  if (data.total_packages === 0) {
    return (
      <EmptyState
        icon={<Box className="h-6 w-6" />}
        title="No external dependencies recorded"
        description="No supported manifests declared third-party packages in this repository scope."
      />
    );
  }

  return (
    <div className="space-y-5">
      <dl className="grid grid-cols-2 border-y border-[var(--color-border-default)] sm:grid-cols-3 lg:grid-cols-6">
        {[
          ["Packages", data.total_packages],
          ["Declarations", data.total_declarations],
          ["Manifests", data.manifest_count],
          ["Observed", data.observed_packages],
          ["Dev-only", data.dev_only_packages],
          ["No graph link", data.unlinked_packages],
        ].map(([label, value]) => (
          <div key={String(label)} className="border-b border-[var(--color-table-divider)] px-3 py-3 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0">
            <dt className="font-mono text-2xs uppercase tracking-wider text-[var(--color-text-tertiary)]">{label}</dt>
            <dd className="mt-1 text-[22px] font-semibold tabular-nums text-[var(--color-text-primary)]">{value}</dd>
          </div>
        ))}
      </dl>

      <div className="space-y-3">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-[minmax(240px,1fr)_170px_170px_190px_170px_auto]">
          <label className="relative min-w-0">
            <span className="sr-only">Search packages</span>
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[var(--color-text-tertiary)]" />
            <input
              type="search"
              value={state.query}
              onChange={(event) => onStateChange(updateState(state, { query: event.target.value }))}
              placeholder="Search packages or versions"
              className="h-9 w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] pl-9 pr-3 text-sm text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] focus:ring-1 focus:ring-[var(--color-accent-primary)]"
            />
          </label>
          <Select value={state.ecosystem} onValueChange={(ecosystem) => onStateChange(updateState(state, { ecosystem }))}>
            <SelectTrigger aria-label="Filter by ecosystem"><SelectValue placeholder="All ecosystems" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All ecosystems</SelectItem>
              {data.ecosystems.map((ecosystem) => <SelectItem key={ecosystem} value={ecosystem}>{ecosystem}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={state.role} onValueChange={(role) => onStateChange(updateState(state, { role: role as ExternalDependencyRole }))}>
            <SelectTrigger aria-label="Filter by dependency role"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any role</SelectItem>
              <SelectItem value="runtime">Runtime</SelectItem>
              <SelectItem value="mixed">Runtime + dev</SelectItem>
              <SelectItem value="dev-only">Dev-only</SelectItem>
            </SelectContent>
          </Select>
          <Select value={state.usage} onValueChange={(usage) => onStateChange(updateState(state, { usage: usage as ExternalDependencyUsage }))}>
            <SelectTrigger aria-label="Filter by graph linkage"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any graph state</SelectItem>
              <SelectItem value="observed">Observed imports</SelectItem>
              <SelectItem value="linked-unobserved">Linked, no imports</SelectItem>
              <SelectItem value="unlinked">No graph link</SelectItem>
            </SelectContent>
          </Select>
          <Select value={state.category} onValueChange={(category) => onStateChange(updateState(state, { category }))}>
            <SelectTrigger aria-label="Filter by package category"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any category</SelectItem>
              {categories.map((category) => <SelectItem key={category} value={category}>{category}</SelectItem>)}
            </SelectContent>
          </Select>
          {activeFilters > 0 ? (
            <Button variant="ghost" size="sm" className="h-9" onClick={() => onStateChange({ ...DEFAULT_EXTERNAL_DEPENDENCY_STATE, sort: state.sort, order: state.order })}>
              Clear {activeFilters}
            </Button>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-text-tertiary)]">
          <p aria-live="polite">
            {filtered.length} {data.truncated ? "loaded " : ""}package{filtered.length === 1 ? "" : "s"} match · showing {pageRows.length ? (page - 1) * pageSize + 1 : 0}–{(page - 1) * pageSize + pageRows.length}
          </p>
          {data.truncated ? <p>Search, filters, and sorting apply to {data.returned} loaded packages out of {data.total_packages}.</p> : null}
        </div>
      </div>

      <ResponsiveTable
        rows={pageRows}
        rowKey={(entry) => entry.package_key}
        columns={columns}
        onRowClick={onSelectedChange}
        selectedKey={selected?.package_key}
        sortField={state.sort}
        sortOrder={state.order}
        onSort={handleSort}
        stacked="md"
        caption="External package declarations and persisted import-graph evidence"
        empty={
          <EmptyState
            title="No packages match these filters"
            description="Clear a filter or search for a different package, ecosystem, or version."
            action={{ label: "Clear filters", onClick: () => onStateChange(DEFAULT_EXTERNAL_DEPENDENCY_STATE) }}
          />
        }
      />

      {pageCount > 1 ? (
        <nav className="flex items-center justify-between" aria-label="Package pages">
          <Button variant="outline" size="sm" disabled={page === 1} onClick={() => onStateChange(updateState(state, { page: page - 1 }))}>
            <ChevronLeft className="h-4 w-4" /> Previous
          </Button>
          <span className="font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">Page {page} of {pageCount}</span>
          <Button variant="outline" size="sm" disabled={page === pageCount} onClick={() => onStateChange(updateState(state, { page: page + 1 }))}>
            Next <ChevronRight className="h-4 w-4" />
          </Button>
        </nav>
      ) : null}

      {data.truncated && onLoadMore ? (
        <div className="flex flex-col items-center gap-2 border-t border-[var(--color-border-default)] pt-4 text-center">
          {loadMoreError ? (
            <>
              <p className="text-xs text-[var(--color-error)]">
                More package summaries couldn't be loaded: {loadMoreError}
              </p>
              {onRetryLoadMore ? <Button variant="outline" size="sm" onClick={onRetryLoadMore}>Retry loading more</Button> : null}
            </>
          ) : (
            <>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                {data.total_packages - data.returned} package summaries remain on the server.
              </p>
              <Button variant="outline" size="sm" onClick={onLoadMore} disabled={loadingMore}>
                {loadingMore ? "Loading more…" : `Load next ${Math.min(data.limit, data.total_packages - data.returned)}`}
              </Button>
            </>
          )}
        </div>
      ) : null}

      <PackageInspector
        entry={selected}
        relationshipsOpen={relationshipsOpen}
        relationships={relationships}
        relationshipsLoading={relationshipsLoading}
        relationshipsError={relationshipsError}
        expandedAggregateKey={expandedAggregateKey}
        importingFiles={importingFiles}
        importingFilesLoading={importingFilesLoading}
        importingFilesError={importingFilesError}
        onShowRelationships={onShowRelationships}
        onHideRelationships={onHideRelationships}
        onRetryRelationships={onRetryRelationships}
        onToggleAggregate={onToggleAggregate}
        onFilesPageChange={onFilesPageChange}
        onRetryImportingFiles={onRetryImportingFiles}
        renderFileLink={renderFileLink}
        onClose={() => onSelectedChange(null)}
      />
    </div>
  );
}
