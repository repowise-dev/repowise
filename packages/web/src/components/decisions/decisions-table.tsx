"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { Badge } from "@/components/ui/badge";
import { listDecisions } from "@/lib/api/decisions";
import type { DecisionRecordResponse } from "@/lib/api/types";

const STATUS_VARIANT: Record<string, "default" | "fresh" | "stale" | "outdated" | "outline" | "accent"> = {
  active: "fresh",
  proposed: "accent",
  deprecated: "outdated",
  superseded: "outline",
};

const SOURCE_LABEL: Record<string, string> = {
  inline_marker: "Inline",
  git_archaeology: "Git",
  readme_mining: "Docs",
  cli: "Manual",
};

interface DecisionsTableProps {
  repoId: string;
  initialData?: DecisionRecordResponse[];
}

export function DecisionsTable({ repoId, initialData }: DecisionsTableProps) {
  const [statusFilter, setStatusFilter] = React.useState<string>("all");
  const [sourceFilter, setSourceFilter] = React.useState<string>("all");

  const { data: decisions } = useSWR(
    [`/api/repos/${repoId}/decisions`, statusFilter, sourceFilter],
    () =>
      listDecisions(repoId, {
        status: statusFilter !== "all" ? statusFilter : undefined,
        source: sourceFilter !== "all" ? sourceFilter : undefined,
        include_proposed: true,
        limit: 100,
      }),
    { fallbackData: initialData },
  );

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex gap-3">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-primary)]"
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="proposed">Proposed</option>
          <option value="deprecated">Deprecated</option>
          <option value="superseded">Superseded</option>
        </select>
        <select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
          className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-primary)]"
        >
          <option value="all">All sources</option>
          <option value="inline_marker">Inline markers</option>
          <option value="git_archaeology">Git archaeology</option>
          <option value="readme_mining">Docs mining</option>
          <option value="cli">Manual</option>
        </select>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-[var(--color-border-default)]">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
              <th className="px-4 py-2.5 text-left font-medium text-[var(--color-text-secondary)]">Title</th>
              <th className="px-4 py-2.5 text-left font-medium text-[var(--color-text-secondary)]">Status</th>
              <th className="px-4 py-2.5 text-left font-medium text-[var(--color-text-secondary)]">Source</th>
              <th className="px-4 py-2.5 text-right font-medium text-[var(--color-text-secondary)]">Confidence</th>
              <th className="px-4 py-2.5 text-left font-medium text-[var(--color-text-secondary)]">Tags</th>
              <th className="px-4 py-2.5 text-right font-medium text-[var(--color-text-secondary)]">Staleness</th>
            </tr>
          </thead>
          <tbody>
            {decisions?.map((d) => (
              <tr
                key={d.id}
                className={`border-b border-[var(--color-border-default)] transition-colors hover:bg-[var(--color-bg-elevated)] ${
                  d.status === "proposed" ? "border-l-2 border-l-amber-400" : ""
                }`}
              >
                <td className="px-4 py-2.5" style={{ maxWidth: 0 }}>
                  <Link
                    href={`/repos/${repoId}/decisions/${d.id}`}
                    className="font-medium text-[var(--color-accent-primary)] hover:underline block truncate"
                    title={d.title}
                  >
                    {d.title}
                  </Link>
                </td>
                <td className="px-4 py-2.5">
                  <Badge variant={STATUS_VARIANT[d.status] ?? "outline"}>{d.status}</Badge>
                </td>
                <td className="px-4 py-2.5 text-[var(--color-text-secondary)]">
                  {SOURCE_LABEL[d.source] ?? d.source}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-[var(--color-text-secondary)]">
                  {Math.round(d.confidence * 100)}%
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-wrap gap-1">
                    {d.tags.slice(0, 3).map((tag) => (
                      <span
                        key={tag}
                        className="inline-block rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-xs text-[var(--color-text-tertiary)]"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums">
                  {d.staleness_score > 0.5 ? (
                    <span className="text-red-500">{d.staleness_score.toFixed(1)}</span>
                  ) : d.staleness_score > 0 ? (
                    <span className="text-[var(--color-text-tertiary)]">{d.staleness_score.toFixed(1)}</span>
                  ) : (
                    <span className="text-[var(--color-text-tertiary)]">-</span>
                  )}
                </td>
              </tr>
            ))}
            {!decisions?.length && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-[var(--color-text-tertiary)]">
                  No decisions found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
