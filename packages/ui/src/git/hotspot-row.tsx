"use client";

import { memo } from "react";
import { Bug, Flame, TrendingUp } from "lucide-react";
import type { Hotspot } from "@repowise-dev/types/git";
import { Badge } from "../ui/badge";
import { ChurnBar } from "./churn-bar";
import { formatLOC } from "../lib/format";
import { summarizeFixHistory } from "../lib/fix-history";
import { cn } from "../lib/cn";
import { clickableRowProps, CLICKABLE_ROW_CLS } from "../shared/responsive-table";
import { RowPromptAction } from "../health/row-prompt-action";

/** A row's fix history, or null when it has none. */
export const fixOf = (h: Hotspot) =>
  summarizeFixHistory(h.prior_defect_count, h.last_fix_at, h.bug_magnet);

/**
 * Name first, folder after it in the quiet tone.
 * TODO(eux): swap for the shared FilePath from `@repowise-dev/ui/shared/file-path`
 * once PR 4161 lands on this base; this is the same name-first reading.
 */
function NameFirstPath({ path }: { path: string }) {
  const name = path.split("/").pop() ?? path;
  const dir = path.slice(0, path.length - name.length).replace(/\/$/, "");
  return (
    <span className="flex min-w-0 items-baseline gap-2 font-mono text-xs" title={path}>
      <span className="shrink-0 text-[var(--color-text-primary)] underline-offset-2 group-hover/row:underline">
        {name}
      </span>
      {dir && <span className="min-w-0 truncate text-[var(--color-text-tertiary)]">{dir}</span>}
    </span>
  );
}

const TD = "px-3 py-2.5";

export const HotspotRow = memo(function HotspotRow({
  hotspot: h,
  rank,
  showFixes,
  onSelect,
  onGeneratePrompt,
}: {
  hotspot: Hotspot;
  rank: number;
  showFixes: boolean;
  onSelect?: ((h: Hotspot) => void) | undefined;
  onGeneratePrompt?: ((h: Hotspot) => void) | undefined;
}) {
  const fix = fixOf(h);
  const score = h.temporal_hotspot_score;
  return (
    <tr
      {...(onSelect ? clickableRowProps(() => onSelect(h)) : {})}
      className={cn(
        "group/row border-b border-[var(--color-table-divider)] hover:bg-[var(--color-bg-elevated)] transition-colors",
        onSelect && CLICKABLE_ROW_CLS,
      )}
    >
      <td className={cn(TD, "text-[var(--color-text-tertiary)] tabular-nums text-xs")}>{rank}</td>
      <td className={cn(TD, "min-w-[200px] max-w-[440px]")}>
        <NameFirstPath path={h.file_path} />
      </td>
      <td className={cn(TD, "tabular-nums text-xs text-right")}>
        <span className="inline-flex items-center justify-end gap-1 text-[var(--color-text-secondary)]">
          {h.commit_count_90d}
          {/* Marked only where true: a column of arrows would mark the default. */}
          {h.accelerating && (
            <TrendingUp
              className="h-3 w-3 text-[var(--color-warning)]"
              aria-label="Accelerating"
            />
          )}
        </span>
      </td>
      {showFixes && (
        <td className={cn(TD, "tabular-nums text-xs text-right")}>
          {fix ? (
            <span
              className={cn(
                "inline-flex items-center justify-end gap-1",
                fix.magnet ? "text-[var(--color-error)]" : "text-[var(--color-text-secondary)]",
              )}
              title={fix.magnet ? `Bug magnet: ${fix.label}` : `Bug fixes: ${fix.label}`}
            >
              {fix.magnet && <Bug className="h-3 w-3 shrink-0" />}
              {fix.count}
              {/* The magnet emphasis never travels without its age. */}
              {fix.magnet && <span className="text-[10px] font-normal">{fix.age}</span>}
            </span>
          ) : (
            <span className="text-[var(--color-text-tertiary)]">—</span>
          )}
        </td>
      )}
      <td className={cn(TD, "hidden lg:table-cell")}>
        <div className="flex items-center gap-2">
          <ChurnBar percentile={h.churn_percentile} className="w-16" tone="neutral" />
          <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums w-8">
            {Math.round(h.churn_percentile)}%
          </span>
        </div>
      </td>
      <td className={cn(TD, "tabular-nums text-xs text-right")}>
        {score != null ? (
          <span className="inline-flex items-center justify-end gap-1 text-[var(--color-text-secondary)]">
            <Flame className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
            {score.toFixed(2)}
          </span>
        ) : (
          <span className="text-[var(--color-text-tertiary)]">—</span>
        )}
      </td>
      <td className={cn(TD, "hidden md:table-cell")}>
        {/* Only a sole owner is marked; zero means no attributable history. */}
        <span
          className={cn(
            "text-xs tabular-nums",
            h.bus_factor === 1 ? "text-[var(--color-warning)]" : "text-[var(--color-text-secondary)]",
          )}
        >
          {h.bus_factor}
        </span>
      </td>
      <td className={cn(TD, "text-xs tabular-nums hidden lg:table-cell")}>
        <span className="text-[var(--color-success)]">+{formatLOC(h.lines_added_90d)}</span>{" "}
        <span className="text-[var(--color-error)]">-{formatLOC(h.lines_deleted_90d)}</span>
      </td>
      <td className={cn(TD, "text-xs text-[var(--color-text-secondary)] hidden md:table-cell")}>
        {h.primary_owner ?? "—"}
      </td>
      <td className={cn(TD, "text-right")} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-end gap-1">
          {h.is_stable && <Badge variant="fresh">Stable</Badge>}
          {onGeneratePrompt && (
            <RowPromptAction subject={h.file_path} onClick={() => onGeneratePrompt(h)} />
          )}
        </div>
      </td>
    </tr>
  );
});
