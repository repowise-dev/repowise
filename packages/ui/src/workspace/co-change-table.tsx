"use client";

import { Badge } from "../ui/badge";
import { EmptyState } from "../shared/empty-state";
import { VirtualizedTable } from "../shared/virtualized-table";
import { formatDate, formatDateTime } from "../lib/format";
import type { WorkspaceCoChangeEvidence, WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";

interface CoChangeTableProps {
  coChanges: WorkspaceCoChangeEntry[];
  compact?: boolean;
}

// Column-priority hide classes, mirroring the shared ResponsiveTable scale:
// priority 2 hides below md (768px), priority 3 hides below lg (1024px). The
// source/target identity columns (priority 1) are always visible.
const HIDE_BELOW_MD = "max-md:hidden";
const HIDE_BELOW_LG = "max-lg:hidden";

function EvidenceCell({ evidence }: { evidence: WorkspaceCoChangeEvidence }) {
  const authors = evidence.authors ?? [];
  const pairs = evidence.commit_pairs ?? [];
  const maxGap = evidence.max_gap_hours ?? 0;
  if (authors.length === 0 && pairs.length === 0) {
    return <span className="text-xs text-[var(--color-text-tertiary)]">—</span>;
  }
  const detailParts: string[] = [];
  if (authors.length > 0) detailParts.push(`by ${authors.slice(0, 2).join(", ")}${authors.length > 2 ? " …" : ""}`);
  if (maxGap >= 0 && pairs.length > 0) detailParts.push(`max gap ${Math.round(maxGap)}h`);
  return (
    <details className="group text-xs">
      <summary
        className="cursor-pointer list-none text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] flex items-center gap-1"
        aria-label="Toggle evidence"
      >
        <span className="inline-block transition-transform group-open:rotate-90">▸</span>
        <span className="tabular-nums">{pairs.length > 0 ? `${pairs.length} commit pair${pairs.length === 1 ? "" : "s"}` : "evidence"}</span>
      </summary>
      <div className="mt-1.5 flex flex-col gap-1.5 pl-3 text-[var(--color-text-tertiary)]">
        {authors.length > 0 && (
          <span className="flex items-center gap-1 whitespace-nowrap">
            <span aria-hidden>👤</span>
            <span>{authors.join(", ")}</span>
          </span>
        )}
        {pairs.map((p, i) => (
          <span key={i} className="flex items-center gap-1 font-mono whitespace-nowrap">
            <span aria-hidden>⛏</span>
            <span className="tabular-nums">{p.source_sha}</span>
            <span aria-hidden>→</span>
            <span className="tabular-nums">{p.target_sha}</span>
            <span className="text-[var(--color-text-tertiary)]">{p.date} · {p.gap_hours}h</span>
          </span>
        ))}
        {detailParts.length > 0 && (
          <span className="text-[var(--color-text-tertiary)]">{detailParts.join(" · ")}</span>
        )}
      </div>
    </details>
  );
}

/**
 * Cross-repo co-change list: one row per file pair that changed together.
 *
 * The body is virtualized (windowed `<tbody>`) so long co-change lists stay
 * cheap to render; below the wrapper's threshold every row renders, so the
 * common short list behaves exactly as a plain table.
 */
export function CoChangeTable({ coChanges, compact }: CoChangeTableProps) {
  if (coChanges.length === 0) {
    return (
      <EmptyState
        title="No cross-repo co-changes"
        description="No files in sibling repos have changed together yet."
      />
    );
  }

  const header = (
    <tr className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider">
      <th className="px-3 py-2 text-left font-medium">Source</th>
      <th className="px-3 py-2 text-left font-medium">Target</th>
      <th className={`px-3 py-2 text-left font-medium w-32 ${HIDE_BELOW_MD}`}>
        <span
          title="Share of the less-active file's recent work sessions that also touched the partner file (same author, recency-weighted). 100% would mean they always change together. It is not a verified dependency."
          className="cursor-help underline decoration-dotted underline-offset-2"
        >
          Strength
        </span>
      </th>
      {compact ? null : (
        <>
          <th className={`px-3 py-2 text-right font-medium ${HIDE_BELOW_LG}`}>Freq</th>
          <th className={`px-3 py-2 text-left font-medium ${HIDE_BELOW_LG}`}>Last</th>
          {compact ? null : (
            <th className={`px-3 py-2 text-right font-medium ${HIDE_BELOW_LG}`}>
              <span
                title="Supporting evidence for the pair: authors, example matched commit pairs, and the time gap between them. Sampled and capped, so a pair backed by hundreds of commits shows three examples."
                className="cursor-help underline decoration-dotted underline-offset-2"
              >
                Evidence
              </span>
            </th>
          )}
        </>
      )}
    </tr>
  );

  const renderRow = (cc: WorkspaceCoChangeEntry) => (
    <tr className="border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]">
      <td className="px-3 py-2 text-left min-w-[160px] max-w-[280px]">
        <div className="flex flex-col gap-0.5">
          <Badge variant="default" className="w-fit text-xs">{cc.source_repo}</Badge>
          <span className="text-xs font-mono text-[var(--color-text-secondary)] truncate block" title={cc.source_file}>
            {cc.source_file}
          </span>
        </div>
      </td>
      <td className="px-3 py-2 text-left min-w-[160px] max-w-[280px]">
        <div className="flex flex-col gap-0.5">
          <Badge variant="default" className="w-fit text-xs">{cc.target_repo}</Badge>
          <span className="text-xs font-mono text-[var(--color-text-secondary)] truncate block" title={cc.target_file}>
            {cc.target_file}
          </span>
        </div>
      </td>
      <td className={`px-3 py-2 text-left ${HIDE_BELOW_MD}`}>
        <div className="flex items-center gap-2 min-w-[90px]">
          <div className="h-1.5 flex-1 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
            <div
              className="h-full rounded-full bg-[var(--color-accent-primary)] transition-all"
              style={{ width: `${Math.min(Math.round(cc.strength * 100), 100)}%` }}
            />
          </div>
          <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8 text-right">
            {`${Math.round(cc.strength * 100)}%`}
          </span>
        </div>
      </td>
      {compact ? null : (
        <>
          <td className={`px-3 py-2 text-right text-xs text-[var(--color-text-secondary)] tabular-nums ${HIDE_BELOW_LG}`}>
            {`${cc.frequency}x`}
          </td>
          <td className={`px-3 py-2 text-left text-xs text-[var(--color-text-tertiary)] ${HIDE_BELOW_LG}`}>
            {/* formatDate, not toLocaleDateString(): the bare call resolves
                the ambient locale, so Node and the browser can render the same
                date differently and hydration fails. */}
            <span title={cc.last_date ? formatDateTime(cc.last_date) : undefined}>
              {cc.last_date ? formatDate(cc.last_date) : "—"}
            </span>
          </td>
          <td className={`px-3 py-2 text-left ${HIDE_BELOW_LG}`}>
            {cc.evidence ? <EvidenceCell evidence={cc.evidence} /> : <span className="text-xs text-[var(--color-text-tertiary)]">—</span>}
          </td>
        </>
      )}
    </tr>
  );

  return (
    <VirtualizedTable<WorkspaceCoChangeEntry>
      rows={coChanges}
      rowKey={(cc) => `${cc.source_repo}|${cc.source_file}|${cc.target_repo}|${cc.target_file}`}
      header={header}
      renderRow={renderRow}
      aria-label="Cross-repo co-changed files"
    />
  );
}
