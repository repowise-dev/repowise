import { Activity, Bug, FileSymlink, History } from "lucide-react";
import { ChurnBar } from "./churn-bar";

export interface ChangeHistoryCardProps {
  /** Repo-wide percentile rank of change entropy, 0–100. */
  changeEntropyPct?: number | null;
  /** Bug-fix commits touching this file in the trailing defect window. */
  priorDefectCount?: number | null;
  /** The file's path before its most recent rename, if any. */
  originalPath?: string | null;
  /** True when the per-file commit-history cap was hit during indexing. */
  commitCountCapped?: boolean;
  className?: string;
}

/**
 * Surfaces the change-complexity + defect-history signals the indexer captures
 * on every run but the product never showed: change entropy (how scattered a
 * file's commits are — Hassan's History Complexity Metric), prior bug-fix
 * count, and rename lineage.
 */
export function ChangeHistoryCard({
  changeEntropyPct,
  priorDefectCount,
  originalPath,
  commitCountCapped,
  className,
}: ChangeHistoryCardProps) {
  const hasEntropy = changeEntropyPct != null && changeEntropyPct > 0;
  const hasDefects = priorDefectCount != null && priorDefectCount > 0;
  const hasRename = !!originalPath;

  // Nothing meaningful to show — let the caller omit the section entirely.
  if (!hasEntropy && !hasDefects && !hasRename && !commitCountCapped) return null;

  return (
    <div className={className}>
      <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
        Change Complexity & Defect History
      </p>
      <div className="space-y-2.5">
        {hasEntropy && (
          <div className="space-y-1">
            <div className="flex items-center gap-1.5 text-xs">
              <Activity className="h-3 w-3 text-[var(--color-text-tertiary)]" />
              <span className="flex-1 text-[var(--color-text-tertiary)]">
                Change entropy
              </span>
              <span
                className="text-[var(--color-text-secondary)] tabular-nums"
                title="How scattered this file's commits are across the codebase (Hassan History Complexity Metric), as a repo-wide percentile."
              >
                {Math.round(changeEntropyPct as number)}%
              </span>
            </div>
            <ChurnBar percentile={changeEntropyPct as number} />
          </div>
        )}

        {priorDefectCount != null && (
          <div className="flex items-center gap-1.5 text-xs">
            <Bug
              className={`h-3 w-3 ${hasDefects ? "text-[var(--color-error)]" : "text-[var(--color-text-tertiary)]"}`}
            />
            <span className="flex-1 text-[var(--color-text-tertiary)]">
              Bug-fix history
            </span>
            <span
              className={`tabular-nums ${hasDefects ? "text-[var(--color-error)]" : "text-[var(--color-text-secondary)]"}`}
              title="Bug-fix commits that touched this file in the trailing defect window."
            >
              {priorDefectCount} {priorDefectCount === 1 ? "fix" : "fixes"}
            </span>
          </div>
        )}

        {hasRename && (
          <div className="flex items-start gap-1.5 text-xs">
            <FileSymlink className="h-3 w-3 shrink-0 mt-0.5 text-[var(--color-text-tertiary)]" />
            <span className="text-[var(--color-text-tertiary)] shrink-0">
              Renamed from
            </span>
            <span
              className="font-mono text-[10px] text-[var(--color-text-secondary)] break-all"
              title={originalPath as string}
            >
              {originalPath}
            </span>
          </div>
        )}

        {commitCountCapped && (
          <div className="flex items-center gap-1.5 text-[10px] text-[var(--color-text-tertiary)]">
            <History className="h-3 w-3 shrink-0" />
            <span>History capped during indexing — older commits not analysed.</span>
          </div>
        )}
      </div>
    </div>
  );
}
