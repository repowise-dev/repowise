"use client";

/**
 * The two things beside the canvas: what is selected, and how to reach the
 * rest of the field without a pointer.
 *
 * The rail used to hold the repository's top findings permanently, which made
 * it a second findings list rather than an inspector: it never described the
 * object under the cursor, and it never changed when the lens did. An
 * inspector explains a selection, so it renders when there is one; the ranked
 * list is what fills the space otherwise, and it is also the field's keyboard
 * and screen-reader route into itself.
 */

import { useMemo } from "react";
import { cn } from "../../lib/cn";
import { scoreBadgeClass } from "../tokens";
import {
  PERFORMANCE_STATE_LABEL,
  performanceBurden,
  performanceFill,
  performanceSentence,
} from "./lens";
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
  if (omitted === 0) return ". Every file with an open cause is drawn.";
  return `. ${omitted.toLocaleString()} more carry causes outside the drawn field.`;
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
  return [...files].sort((a, b) => a.score - b.score || a.file_path.localeCompare(b.file_path));
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
                    ? "bg-[var(--color-accent-muted)]"
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
                      {f.score.toFixed(1)}
                    </span>
                  )}
                </span>
                <span className="truncate font-mono text-[10px] text-[var(--color-text-tertiary)]">
                  {f.file_path}
                </span>
                {overlay === "performance" ? (
                  <span className="text-[11px] text-[var(--color-text-tertiary)]">
                    {performanceSentence(f)}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
      <p className="text-[11px] text-[var(--color-text-tertiary)]">
        <span className="tabular-nums">{rows.length}</span> of{" "}
        <span className="tabular-nums">{ranked.length.toLocaleString()}</span>{" "}
        {overlay === "performance" ? "files with an open cause" : "drawn files"} listed
        {overlay === "performance" ? causesOutside(scope) : "."}
      </p>
    </div>
  );
}

/**
 * The selected file, described by the active lens.
 *
 * Hover is pointer-bound decoding on the canvas; this opens for a click or a
 * keyboard selection and stays until it is dismissed, which is the difference
 * between reading a node and inspecting one.
 */
export function MapInspector({
  file,
  overlay,
  onOpen,
  onClose,
}: {
  file: CodeHealthMapFile;
  overlay: CodeHealthOverlay;
  onOpen: (path: string) => void;
  onClose: () => void;
}) {
  const name = file.file_path.split("/").pop() ?? file.file_path;
  const burden = performanceBurden(file);
  return (
    <section
      aria-label={`Inspecting ${file.file_path}`}
      className="flex flex-col gap-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3"
    >
      <div className="flex items-center gap-2">
        {/* The lens decides what leads. Under performance the defect score is
            not the subject, and putting its coloured badge first told the
            reader the file was fine while the mark beside it said otherwise.

            The mark is the node as the canvas draws it, from the same
            function, so the selection and the field it was picked out of can
            never describe the file differently. */}
        {overlay === "performance" ? (
          <span
            aria-hidden
            className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full"
            style={{ backgroundColor: performanceFill(file) }}
          />
        ) : (
          <span
            className={cn(
              "inline-flex shrink-0 items-center justify-center rounded px-1.5 py-0.5 text-xs font-semibold tabular-nums",
              scoreBadgeClass(file.score),
            )}
          >
            {file.score.toFixed(1)}
          </span>
        )}
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--color-text-primary)]">
          {name}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Clear selection"
          className="shrink-0 rounded px-1.5 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
        >
          ✕
        </button>
      </div>

      <div className="truncate font-mono text-xs text-[var(--color-text-tertiary)]">
        {file.file_path}
      </div>

      {overlay === "performance" ? (
        <dl className="flex flex-col gap-1 border-t border-[var(--color-border-default)] pt-2 text-xs">
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-[var(--color-text-tertiary)]">
              Open {burden.unit}
            </dt>
            <dd className="font-mono text-base tabular-nums text-[var(--color-text-primary)]">
              {burden.count}
            </dd>
          </div>
          {file.performance_observations != null && burden.unit === "opportunities" ? (
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-[var(--color-text-tertiary)]">Observations behind them</dt>
              <dd className="font-mono tabular-nums text-[var(--color-text-primary)]">
                {file.performance_observations}
              </dd>
            </div>
          ) : null}
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-[var(--color-text-tertiary)]">State</dt>
            <dd className="text-right text-[var(--color-text-primary)]">
              {PERFORMANCE_STATE_LABEL[burden.state]}
            </dd>
          </div>
        </dl>
      ) : null}

      <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-[var(--color-border-default)] pt-2 text-xs text-[var(--color-text-secondary)]">
        {overlay === "performance" ? (
          // Still available, but as one supporting figure among several rather
          // than as the headline the lens is not about.
          <span className="tabular-nums">defect risk {file.score.toFixed(1)}</span>
        ) : null}
        <span className="tabular-nums">{file.nloc.toLocaleString()} NLOC</span>
        {file.line_coverage_pct != null ? (
          <span className="tabular-nums">{Math.round(file.line_coverage_pct)}% coverage</span>
        ) : null}
        <span>{file.has_test_file ? "has tests" : "untested"}</span>
      </div>

      <button
        type="button"
        onClick={() => onOpen(file.file_path)}
        className="w-full rounded-md border border-[var(--color-border-default)] px-2 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
      >
        Open details
      </button>
    </section>
  );
}
