"use client";

import * as React from "react";
import { ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, FileCode2, Network } from "lucide-react";
import type {
  ExternalSystemImportingFiles,
  ExternalSystemRelationshipGraph as RelationshipGraph,
} from "@repowise-dev/types/external-systems";
import { Button } from "../ui/button";

export interface PackageRelationshipGraphProps {
  packageLabel: string;
  graph: RelationshipGraph;
  expandedAggregateKey?: string | null | undefined;
  files?: ExternalSystemImportingFiles | undefined;
  filesLoading?: boolean | undefined;
  filesError?: string | null | undefined;
  renderFileLink?: ((path: string, children: React.ReactNode) => React.ReactNode) | undefined;
  onBack: () => void;
  onToggleAggregate: (aggregateKey: string | null) => void;
  onFilesPageChange: (offset: number) => void;
  onRetryFiles?: (() => void) | undefined;
}

function matchDescription(graph: RelationshipGraph): string {
  if (graph.match_basis === "unresolved") return "No linked external graph target was found.";
  if (graph.match_basis === "mixed") return "Evidence combines multiple graph match methods.";
  if (graph.match_basis === "mapped") return "Evidence uses a persisted package mapping.";
  if (graph.match_basis === "subpath") return "Evidence matched a package subpath.";
  return "Evidence matched the package exactly.";
}

export function PackageRelationshipGraph({
  packageLabel,
  graph,
  expandedAggregateKey,
  files,
  filesLoading,
  filesError,
  renderFileLink,
  onBack,
  onToggleAggregate,
  onFilesPageChange,
  onRetryFiles,
}: PackageRelationshipGraphProps) {
  return (
    <section aria-labelledby="package-relationship-heading" className="space-y-4">
      <div className="flex items-start gap-3">
        <Button variant="ghost" size="sm" onClick={onBack} aria-label="Back to package details">
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="min-w-0">
          <h3 id="package-relationship-heading" className="text-sm font-semibold text-[var(--color-text-primary)]">
            Package relationships
          </h3>
          <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
            {graph.importing_file_total} importing file{graph.importing_file_total === 1 ? "" : "s"} grouped into {graph.aggregate_total} first-party area{graph.aggregate_total === 1 ? "" : "s"}.
          </p>
        </div>
      </div>

      <div
        className="rounded-lg border border-[var(--color-accent-primary)]/35 bg-[var(--color-accent-fill)] px-3 py-3"
        role="group"
        aria-label={`Focused external package ${packageLabel}`}
      >
        <div className="flex items-center gap-2">
          <Network className="h-4 w-4 text-[var(--color-accent-primary)]" />
          <span className="min-w-0 break-words font-mono text-xs font-semibold text-[var(--color-text-primary)]">
            {packageLabel}
          </span>
        </div>
        <p className="mt-1 text-2xs text-[var(--color-text-tertiary)]">
          External package focus · {matchDescription(graph)}
        </p>
      </div>

      {graph.nodes.length ? (
        <ul className="space-y-2 border-l border-[var(--color-border-default)] pl-3" aria-label="First-party areas">
          {graph.nodes.map((node) => {
            const expanded = expandedAggregateKey === node.aggregate_key;
            return (
              <li key={node.aggregate_key}>
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => onToggleAggregate(expanded ? null : node.aggregate_key)}
                  className="group flex w-full items-center gap-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2.5 text-left hover:border-[var(--color-accent-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] motion-reduce:transition-none"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block break-words text-xs font-medium text-[var(--color-text-primary)]">{node.label}</span>
                    <span className="mt-0.5 block text-2xs text-[var(--color-text-tertiary)]">
                      {node.importing_file_count} file{node.importing_file_count === 1 ? "" : "s"} · {node.import_edge_count} edge{node.import_edge_count === 1 ? "" : "s"}
                    </span>
                  </span>
                  <ArrowRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] group-hover:text-[var(--color-accent-primary)]" />
                </button>

                {expanded ? (
                  <div className="ml-3 border-l border-[var(--color-border-default)] py-3 pl-3">
                    {filesLoading && !files ? (
                      <p className="text-xs text-[var(--color-text-tertiary)]" role="status">Loading importing files…</p>
                    ) : filesError ? (
                      <div className="space-y-2" role="alert">
                        <p className="text-xs text-[var(--color-text-secondary)]">{filesError}</p>
                        {onRetryFiles ? <Button variant="outline" size="sm" onClick={onRetryFiles}>Retry</Button> : null}
                      </div>
                    ) : files?.items.length ? (
                      <>
                        <ul className="space-y-2" aria-label={`Importing files in ${node.label}`}>
                          {files.items.map((file) => (
                            <li key={file.path} className="flex items-start gap-2 text-xs">
                              <FileCode2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
                              <span className="min-w-0 flex-1 break-all font-mono text-[var(--color-text-secondary)]">
                                {renderFileLink ? renderFileLink(file.path, file.path) : file.path}
                              </span>
                              <span className="shrink-0 text-2xs text-[var(--color-text-tertiary)]">{file.import_edge_count}</span>
                            </li>
                          ))}
                        </ul>
                        <div className="mt-3 flex items-center justify-between gap-2 text-2xs text-[var(--color-text-tertiary)]">
                          <span>{files.offset + 1}–{files.offset + files.returned} of {files.total}</span>
                          <div className="flex gap-1">
                            <Button
                              variant="outline"
                              size="sm"
                              aria-label="Previous importing files"
                              disabled={files.offset === 0}
                              onClick={() => onFilesPageChange(Math.max(0, files.offset - files.limit))}
                            >
                              <ChevronLeft className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              aria-label="Next importing files"
                              disabled={!files.truncated}
                              onClick={() => onFilesPageChange(files.offset + files.limit)}
                            >
                              <ChevronRight className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </div>
                      </>
                    ) : (
                      <p className="text-xs text-[var(--color-text-tertiary)]">No importing files were returned for this area.</p>
                    )}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="rounded-lg border border-dashed border-[var(--color-border-default)] px-4 py-5 text-center">
          <p className="text-sm font-medium text-[var(--color-text-primary)]">No relationship evidence</p>
          <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
            {graph.match_basis === "unresolved"
              ? "This declared package is not linked to a persisted external graph target."
              : "The linked graph target has no persisted first-party import edges in this scope."}
          </p>
        </div>
      )}

      {(graph.truncated || graph.matched_external_nodes_truncated) ? (
        <p className="rounded-md bg-[var(--color-bg-elevated)] px-3 py-2 text-xs text-[var(--color-text-secondary)]" role="status">
          Showing {graph.aggregate_returned} of {graph.aggregate_total} areas and {graph.edge_returned} of {graph.edge_total} relationships. Limits: {graph.node_limit} areas and {graph.edge_limit} edges.
          {graph.matched_external_nodes_truncated ? ` Showing ${graph.matched_external_nodes.length} of ${graph.matched_external_nodes_total} matched external targets.` : ""}
          {graph.evidence_truncated ? ` Import evidence is partial because more than ${graph.evidence_target_limit} external targets matched.` : ""}
        </p>
      ) : (
        <p className="text-2xs text-[var(--color-text-tertiary)]">
          {graph.aggregate_returned} area{graph.aggregate_returned === 1 ? "" : "s"} · {graph.edge_returned} relationship{graph.edge_returned === 1 ? "" : "s"} · bounded to {graph.node_limit} areas / {graph.edge_limit} edges
        </p>
      )}
    </section>
  );
}
