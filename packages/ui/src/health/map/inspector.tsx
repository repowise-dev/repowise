"use client";

/**
 * The ranked list beside the canvas: how to reach the field without a pointer.
 *
 * It ranks what the active lens marks, so it changes when the lens does, and
 * it is the field's keyboard and screen-reader route into itself.
 */

import { useMemo } from "react";
import { cn } from "../../lib/cn";
import { formatScore } from "@repowise-dev/types/health";
import { performanceBurden, performanceSentence } from "./lens";
import type { CodeHealthMapFile, CodeHealthOverlay, MapScope } from "./types";

/** Rows the ranked list draws. Past this it is an inventory, not a lead. */
export const FIELD_LIST_CAP = 50;

/**
 * What the list can say about causes it is not showing.
 *
 * Three answers, and a missing scope is the third one. A host that wires no
 * scope knows nothing about the field beyond it, and printing nothing there
 * would read as "there is nothing beyond it".
 */
function causesOutside(scope: MapScope | undefined): string {
  const omitted = scope?.omitted.performanceFiles;
  if (scope === undefined || omitted === null || omitted === undefined) {
    return ". Whether more carry causes outside the drawn field is not reported here.";
  }
  if (omitted === 0) return ". Every file with a cause worth fixing now is drawn.";
  return `. ${omitted.toLocaleString()} more carry causes worth fixing now outside the drawn field.`;
}

/** Worst-first for the active lens, so the list ranks what the field marks. */
function rankFiles(
  files: CodeHealthMapFile[],
  overlay: CodeHealthOverlay,
): CodeHealthMapFile[] {
  if (overlay === "performance") {
    return files
      .filter((f) => performanceBurden(f).count > 0)
      .sort((a, b) => {
        const ar = a.performance_rank ?? Number.MAX_SAFE_INTEGER;
        const br = b.performance_rank ?? Number.MAX_SAFE_INTEGER;
        if (ar !== br) return ar - br;
        const ac = performanceBurden(a).count;
        const bc = performanceBurden(b).count;
        if (ac !== bc) return bc - ac;
        return a.file_path.localeCompare(b.file_path);
      });
  }
  // A file with no score sorts last: nothing measured it, so it is neither
  // worst nor best.
  const key = (f: CodeHealthMapFile) => f.score ?? Number.POSITIVE_INFINITY;
  return [...files].sort(
    (a, b) => key(a) - key(b) || a.file_path.localeCompare(b.file_path),
  );
}

const LIST_HEADING: Record<string, string> = {
  performance: "Files by performance pressure",
  maintainability: "Files by maintainability",
  health: "Files by health",
};

/**
 * A bounded ranked list of the drawn files, in the active lens's order.
 *
 * This is the canvas's navigable alternative: every row is a real button, so
 * a keyboard and a screen reader reach the same objects a pointer does and
 * open the same inspector, without turning the field itself into thousands of
 * tab stops. It states its own truncation, because a list that silently stops
 * at fifty reads as a repository with fifty problems.
 */
export function MapFieldList({
  files,
  overlay,
  selectedPath,
  onSelectFile,
  scope,
}: {
  files: CodeHealthMapFile[];
  overlay: CodeHealthOverlay;
  selectedPath?: string | null;
  onSelectFile: (path: string) => void;
  scope?: MapScope;
}) {
  const ranked = useMemo(() => rankFiles(files, overlay), [files, overlay]);
  const rows = ranked.slice(0, FIELD_LIST_CAP);
  const heading = LIST_HEADING[overlay] ?? "Files in this field";

  if (rows.length === 0) {
    return (
      <div className="text-xs text-[var(--color-text-tertiary)]">
        {overlay === "performance"
          ? "No file in the drawn field carries an open performance cause."
          : "Nothing to rank in the drawn field yet."}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {heading}
      </h3>
      <ul className="flex min-h-0 flex-col divide-y divide-[var(--color-border-default)] overflow-y-auto">
        {rows.map((f) => {
          const selected = f.file_path === selectedPath;
          const name = f.file_path.split("/").pop() ?? f.file_path;
          return (
            <li key={f.file_path}>
              <button
                type="button"
                onClick={() => onSelectFile(f.file_path)}
                aria-current={selected ? "true" : undefined}
                className={cn(
                  "flex w-full flex-col gap-0.5 px-1.5 py-2 text-left transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]",
                  selected
                    ? "bg-[var(--color-bg-selected)] shadow-[inset_2px_0_0_var(--color-accent-primary)]"
                    : "hover:bg-[var(--color-bg-elevated)]",
                )}
              >
                <span className="flex items-baseline gap-2">
                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-[var(--color-text-primary)]">
                    {name}
                  </span>
                  {overlay === "performance" ? (
                    <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-secondary)]">
                      {performanceBurden(f).count}
                    </span>
                  ) : (
                    <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-secondary)]">
                      {f.score == null ? "—" : formatScore(f.score)}
                    </span>
                  )}
                </span>
                <span className="truncate font-mono text-[10px] text-[var(--color-text-tertiary)]">
                  {f.file_path}
                </span>
                {overlay === "performance" ? (
                  <span className="text-xs text-[var(--color-text-tertiary)]">
                    {performanceSentence(f)}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-[var(--color-text-tertiary)]">
        <span className="tabular-nums">{rows.length}</span> of{" "}
        <span className="tabular-nums">{ranked.length.toLocaleString()}</span>{" "}
        {overlay === "performance" ? "files with a cause worth fixing now" : "drawn files"} listed
        {overlay === "performance" ? causesOutside(scope) : "."}
      </p>
    </div>
  );
}
