"use client";

import * as React from "react";
import { EmptyState } from "../shared/empty-state";
import { InfoTip } from "../shared/info-tip";
import { clickableRowProps, CLICKABLE_ROW_CLS } from "../shared/responsive-table";
import { cn } from "../lib/cn";
import { formatDate, formatDateTime } from "../lib/format";
import { PAIR_MICRO as MICRO } from "../coupling/pair-drawer-parts";
import { SESSIONS_DEFINITION, STRENGTH_DEFINITION } from "./co-change-facts";
import type { WorkspaceCoChangeEvidence, WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";

interface CoChangeTableProps {
  coChanges: WorkspaceCoChangeEntry[];
  /** Drops the sessions and date columns, for previews. */
  compact?: boolean;
  /** Makes rows open the pair; omitted, rows are plain. */
  onSelect?: (cc: WorkspaceCoChangeEntry) => void;
  /** `coChangeKey` of the open pair, marked as selected. */
  selectedKey?: string | null;
}

/**
 * Stable identity for one file pair, used for row keys and the `?cc=` param.
 * Order-independent, so a row flipped to lead with a searched file keeps it.
 */
export function coChangeKey(cc: WorkspaceCoChangeEntry): string {
  const a = `${cc.source_repo}:${cc.source_file}`;
  const b = `${cc.target_repo}:${cc.target_file}`;
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}


// Priority 2 hides below md, matching the shared ResponsiveTable scale. The two
// files and the strength stay at every width.
const HIDE_BELOW_MD = "max-md:hidden";

/**
 * The bounded sample behind one pair: which authors, which matched commits, and
 * how far apart they landed. Collapsed by default, so a reader scans the list
 * and opens only the pair they doubt. The counts are small by construction
 * (`_MAX_EVIDENCE_COMMIT_PAIRS`, `_MAX_EVIDENCE_AUTHORS` in the miner), so this
 * never renders an unbounded block.
 */
function EvidenceCell({ evidence }: { evidence: WorkspaceCoChangeEvidence }) {
  const authors = evidence.authors ?? [];
  const pairs = evidence.commit_pairs ?? [];
  if (authors.length === 0 && pairs.length === 0) {
    return <span className="text-[var(--color-text-tertiary)]">none recorded</span>;
  }
  return (
    <details className="group">
      <summary className="flex cursor-pointer list-none items-center justify-end gap-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
        <span className="inline-block transition-transform group-open:rotate-90" aria-hidden>
          ▸
        </span>
        <span className="tabular-nums">
          {pairs.length > 0 ? `${pairs.length} commit pair${pairs.length === 1 ? "" : "s"}` : "evidence"}
        </span>
      </summary>
      <div className="mt-1.5 flex flex-col items-end gap-1 text-[var(--color-text-tertiary)]">
        {authors.length > 0 ? <span className="whitespace-nowrap">{authors.join(", ")}</span> : null}
        {pairs.map((pair) => (
          <span key={`${pair.source_sha}|${pair.target_sha}`} className="whitespace-nowrap font-mono">
            <span className="tabular-nums">{pair.source_sha}</span>
            <span aria-hidden> → </span>
            <span className="tabular-nums">{pair.target_sha}</span>
            <span>{` · ${pair.gap_hours}h`}</span>
          </span>
        ))}
        {evidence.max_gap_hours > 0 ? (
          <span>{`max gap ${evidence.max_gap_hours}h`}</span>
        ) : null}
      </div>
    </details>
  );
}

function splitPath(path: string): [dir: string, base: string] {
  const i = path.lastIndexOf("/");
  return i < 0 ? ["", path] : [path.slice(0, i + 1), path.slice(i + 1)];
}

/**
 * A repository as a quiet micro-label over its path: directory dim, filename
 * full strength and never cut, so the part a reader recognises survives any
 * width. In a row the directory truncates (full path in the DOM and the
 * title); with `wrap`, where the exact file must be legible, lines break only
 * after a "/".
 */
export function RepoPath({ repo, path, wrap = false }: { repo: string; path: string; wrap?: boolean }) {
  const [dir, base] = splitPath(path);
  if (wrap) {
    return (
      <div className="min-w-0">
        <span className={cn(MICRO, "block")}>{repo}</span>
        <p className="min-w-0 font-mono text-xs leading-5 [overflow-wrap:break-word]">
          {dir.split("/").filter(Boolean).map((seg, i) => (
            <React.Fragment key={i}>
              <span className="text-[var(--color-text-tertiary)]">{seg}/</span>
              <wbr />
            </React.Fragment>
          ))}
          <span className="font-medium text-[var(--color-text-primary)]">{base}</span>
        </p>
      </div>
    );
  }
  return (
    <div className="min-w-0">
      <span className={cn(MICRO, "block")}>{repo}</span>
      <span className="flex min-w-0 font-mono text-xs leading-5" title={path}>
        {dir && <span className="truncate text-[var(--color-text-tertiary)]">{dir}</span>}
        <span className="max-w-full shrink-0 font-medium text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
          {base}
        </span>
      </span>
    </div>
  );
}

interface RowProps {
  cc: WorkspaceCoChangeEntry;
  compact: boolean;
  selected: boolean;
  onSelect: ((cc: WorkspaceCoChangeEntry) => void) | undefined;
}

/** Memoised so opening a pair or narrowing the list re-renders only the rows that changed. */
const CoChangeRow = React.memo(function CoChangeRow({ cc, compact, selected, onSelect }: RowProps) {
  const interactive = onSelect !== undefined;
  return (
    <tr
      className={cn(
        "border-t border-[var(--color-border-default)] align-top",
        interactive && "hover:bg-[var(--color-bg-elevated)]",
        selected && "bg-[var(--color-accent-muted)]",
        interactive && CLICKABLE_ROW_CLS,
      )}
      aria-current={selected ? "true" : undefined}
      {...(interactive ? clickableRowProps(() => onSelect(cc)) : {})}
    >
      <td className="px-3 py-2.5 text-left">
        <RepoPath repo={cc.source_repo} path={cc.source_file} />
      </td>
      <td className="px-3 py-2.5 text-left">
        <RepoPath repo={cc.target_repo} path={cc.target_file} />
      </td>
      <td className="px-3 py-2.5 text-right align-middle font-mono text-xs tabular-nums text-[var(--color-text-primary)]">
        {Math.round(cc.strength * 100)}%
      </td>
      {compact ? null : (
        <>
          <td
            className={`px-3 py-2.5 text-right align-middle font-mono text-xs tabular-nums text-[var(--color-text-secondary)] ${HIDE_BELOW_MD}`}
          >
            {cc.frequency}
          </td>
          <td
            className={`whitespace-nowrap px-3 py-2.5 text-right align-middle font-mono text-xs tabular-nums text-[var(--color-text-tertiary)] ${HIDE_BELOW_MD}`}
          >
            {/* formatDate, not toLocaleDateString(): the bare call resolves
                the ambient locale, so Node and the browser can render the same
                date differently and hydration fails. */}
            <span title={cc.last_date ? formatDateTime(cc.last_date) : undefined}>
              {cc.last_date ? formatDate(cc.last_date) : "unknown"}
            </span>
          </td>
          <td className={`px-3 py-2.5 text-right align-middle text-xs ${HIDE_BELOW_MD}`}>
            {cc.evidence ? (
              <EvidenceCell evidence={cc.evidence} />
            ) : (
              <span className="text-[var(--color-text-tertiary)]">—</span>
            )}
          </td>
        </>
      )}
    </tr>
  );
});

/**
 * Cross-repo co-change list: one row per file pair that changed together.
 *
 * A plain table in the page flow rather than a windowed inner scroller: the
 * miner stores at most 200 pairs and the endpoint serves at most 500, and a
 * fixed-height scroller left the page empty below it. Rows are memoised
 * instead. If the endpoint ever serves thousands, window it again.
 *
 * No strength bar: in a list sorted by strength, neighbouring rows differ by a
 * point or two, so a bar per row repeats one shape. The figure is exact and
 * aligns.
 */
export function CoChangeTable({ coChanges, compact = false, onSelect, selectedKey }: CoChangeTableProps) {
  if (coChanges.length === 0) {
    return (
      <EmptyState
        title="No cross-repo co-changes"
        description="No files in sibling repos have changed together yet."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse" aria-label="Cross-repo co-changed files">
        <thead>
          <tr className="border-b border-[var(--color-border-default)]">
            <th className={cn(MICRO, "px-3 py-2 text-left font-normal")}>File</th>
            <th className={cn(MICRO, "px-3 py-2 text-left font-normal")}>Changes with</th>
            <th className={cn(MICRO, "px-3 py-2 text-right font-normal")}>
              <span className="inline-flex items-center gap-1">
                Strength
                <InfoTip content={STRENGTH_DEFINITION} label="What strength means" />
              </span>
            </th>
            {compact ? null : (
              <>
                <th className={cn(MICRO, "px-3 py-2 text-right font-normal", HIDE_BELOW_MD)}>
                  <span className="inline-flex items-center gap-1">
                    Sessions
                    <InfoTip content={SESSIONS_DEFINITION} label="What a shared session is" />
                  </span>
                </th>
                <th className={cn(MICRO, "px-3 py-2 text-right font-normal", HIDE_BELOW_MD)}>
                  Last together
                </th>
                <th className={cn(MICRO, "px-3 py-2 text-right font-normal", HIDE_BELOW_MD)}>
                  <span className="inline-flex items-center gap-1">
                    Evidence
                    <InfoTip
                      content="Supporting evidence for the pair: authors, example matched commit pairs, and the time gap between them. Sampled and capped, so a pair backed by hundreds of commits shows a few examples."
                      label="What the evidence column shows"
                    />
                  </span>
                </th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {coChanges.map((cc) => {
            const key = coChangeKey(cc);
            return (
              <CoChangeRow
                key={key}
                cc={cc}
                compact={compact}
                selected={selectedKey === key}
                onSelect={onSelect}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
