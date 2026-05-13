"use client";

import { useState } from "react";
import { truncatePath } from "../lib/format";

interface CoChangeListProps {
  partners: Array<{ file_path: string; co_change_count: number }>;
  /**
   * Number of rows rendered when collapsed. The control toggles to the
   * full list — partners are already trimmed server-side by ``min_count``
   * so the full set is bounded.
   */
  previewCount?: number;
}

export function CoChangeList({ partners, previewCount = 5 }: CoChangeListProps) {
  const [expanded, setExpanded] = useState(false);
  if (partners.length === 0) return null;

  const maxCount = Math.max(...partners.map((p) => p.co_change_count));
  const visible = expanded ? partners : partners.slice(0, previewCount);
  const hidden = partners.length - visible.length;

  return (
    <div className="space-y-1.5">
      {visible.map((p) => (
        <div key={p.file_path} className="space-y-0.5">
          <div className="flex items-center justify-between text-xs">
            <span className="font-mono text-[var(--color-text-secondary)] truncate flex-1 min-w-0" title={p.file_path}>
              {truncatePath(p.file_path, 45)}
            </span>
            <span className="text-[var(--color-text-tertiary)] tabular-nums shrink-0 ml-1">
              {p.co_change_count}&times;
            </span>
          </div>
          <div className="h-1 w-full rounded-full bg-[var(--color-bg-elevated)]">
            <div
              className="h-1 rounded-full bg-[var(--color-accent-primary)] transition-all"
              style={{
                width: `${maxCount > 0 ? (p.co_change_count / maxCount) * 100 : 0}%`,
              }}
            />
          </div>
        </div>
      ))}
      {(hidden > 0 || expanded) && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-[10px] text-[var(--color-accent-primary)] hover:underline"
        >
          {expanded ? "Show fewer" : `Show ${hidden} more`}
        </button>
      )}
    </div>
  );
}
