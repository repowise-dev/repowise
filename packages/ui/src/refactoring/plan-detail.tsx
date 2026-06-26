"use client";

import { ArrowRight, CornerDownRight, FilePlus2, Layers, Scissors } from "lucide-react";
import { typeAccent } from "./meta";
import {
  blastFiles,
  cutEdges,
  cycleMembers,
  extractClassGroups,
  extractHelperOccurrences,
  helperSite,
  moveTarget,
  splitBlast,
  splitGroups,
  splitResidual,
  splitShimRequired,
  type RefactoringPlan,
} from "./types";

export interface PlanDetailProps {
  plan: RefactoringPlan;
  /** Render a file path as a link (e.g. to the file's health drawer). */
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  /** Drop the leading prose line — used when the plan is the "after" column of
   *  a before→after comparison, where the intro would be redundant. */
  hideIntro?: boolean;
}

function shortFile(path: string): string {
  const parts = path.split("/");
  return parts.length <= 2 ? path : `…/${parts.slice(-2).join("/")}`;
}

function FileRef({
  path,
  line,
  fileHref,
  className = "",
}: {
  path: string;
  line?: number | null;
  fileHref?: PlanDetailProps["fileHref"];
  /** Extra classes, e.g. `block truncate` inside a `min-w-0` flex item. */
  className?: string;
}) {
  const inner = (
    <>
      {shortFile(path)}
      {line ? <span className="text-[var(--color-text-tertiary)]">:{line}</span> : null}
    </>
  );
  const href = fileHref?.(path, line ?? null);
  if (!href) {
    return (
      <span className={`font-mono text-xs text-[var(--color-text-secondary)] ${className}`} title={path}>
        {inner}
      </span>
    );
  }
  return (
    <a
      href={href}
      className={`font-mono text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-accent-primary)] hover:underline ${className}`}
      title={path}
    >
      {inner}
    </a>
  );
}

/**
 * The concrete, type-specific body of a refactoring plan: split groups as a
 * tree, the move as an arrow, clone occurrences as a list, the cycle as cut
 * edges. This is the "what exactly to do" — the heart of the surface.
 */
export function PlanDetail({ plan, fileHref, hideIntro = false }: PlanDetailProps) {
  const accent = typeAccent(plan.refactoring_type);

  if (plan.refactoring_type === "extract_class") {
    const groups = extractClassGroups(plan).filter(
      (g) => g.methods.length > 0 || g.fields.length > 0,
    );
    return (
      <div className="space-y-3">
        {hideIntro ? null : (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Split into {groups.length} cohesive group{groups.length === 1 ? "" : "s"} — each
            group's methods travel with the fields they touch.
          </p>
        )}
        <div className="grid gap-2.5 sm:grid-cols-2">
          {groups.map((g, i) => (
            <div
              key={i}
              className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3.5"
            >
              <div className="mb-2 flex items-center gap-2">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: accent }}
                  aria-hidden
                />
                <span className="text-xs font-semibold text-[var(--color-text-primary)]">
                  {g.name ?? `Group ${i + 1}`}
                </span>
              </div>
              {g.fields.length > 0 ? (
                <div className="mb-1.5">
                  <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
                    Fields
                  </span>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {g.fields.map((f) => (
                      <code
                        key={f}
                        className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-secondary)]"
                      >
                        {f}
                      </code>
                    ))}
                  </div>
                </div>
              ) : null}
              <div>
                <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
                  Methods
                </span>
                <ul className="mt-1 space-y-0.5">
                  {g.methods.map((m) => (
                    <li
                      key={m}
                      className="flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-text-secondary)]"
                    >
                      <CornerDownRight className="h-3 w-3 text-[var(--color-text-tertiary)]" />
                      {m}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (plan.refactoring_type === "extract_helper") {
    const occ = extractHelperOccurrences(plan);
    const site = helperSite(plan);
    return (
      <div className="space-y-3">
        {hideIntro ? null : (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            The same block appears in {occ.length} place{occ.length === 1 ? "" : "s"}. Extract
            it once
            {site ? (
              <>
                {" "}
                near{" "}
                <code className="rounded bg-[var(--color-bg-elevated)] px-1 py-0.5 text-[11px] text-[var(--color-text-secondary)]">
                  {site}
                </code>
              </>
            ) : null}
            .
          </p>
        )}
        <ul className="divide-y divide-[var(--color-border-default)] overflow-hidden rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
          {occ.map((o, i) => (
            <li key={i} className="flex items-center justify-between gap-3 px-3.5 py-2.5">
              <span className="min-w-0 flex-1">
                <FileRef path={o.file} line={o.line_start} fileHref={fileHref} className="block truncate" />
              </span>
              <span className="shrink-0 text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
                lines {o.line_start}–{o.line_end}
              </span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (plan.refactoring_type === "move_method") {
    const mv = moveTarget(plan);
    if (!mv) return null;
    return (
      <div className="space-y-3">
        {hideIntro ? null : (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            This method leans on another class's data more than its own — move it home.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
          <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
              From
            </div>
            <code className="text-xs text-[var(--color-text-secondary)]">{mv.from_class}</code>
          </div>
          <div className="flex flex-col items-center text-[var(--color-text-tertiary)]">
            <code className="mb-0.5 text-[11px] text-[var(--color-text-primary)]">{mv.method}</code>
            <ArrowRight className="h-4 w-4" style={{ color: accent }} />
          </div>
          <div
            className="rounded-lg border px-3 py-2"
            style={{ borderColor: accent, backgroundColor: `color-mix(in srgb, ${accent} 8%, transparent)` }}
          >
            <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
              To
            </div>
            <code className="text-xs text-[var(--color-text-primary)]">{mv.to_class}</code>
            {mv.to_file ? (
              <div className="mt-0.5">
                <FileRef path={mv.to_file} fileHref={fileHref} />
              </div>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  if (plan.refactoring_type === "break_cycle") {
    const members = cycleMembers(plan);
    const edges = cutEdges(plan);
    return (
      <div className="space-y-3">
        {hideIntro ? null : (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            {members.length} files import each other in a cycle. Cut {edges.length} edge
            {edges.length === 1 ? "" : "s"} to break it.
          </p>
        )}
        <div className="space-y-2 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3.5">
          <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
            Cut these import edges
          </div>
          {edges.map((e, i) => (
            <div key={i} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
              <Scissors className="h-3.5 w-3.5 shrink-0" style={{ color: accent }} />
              <FileRef path={e.from} fileHref={fileHref} className="min-w-0 truncate" />
              <ArrowRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
              <FileRef path={e.to} fileHref={fileHref} className="min-w-0 truncate" />
            </div>
          ))}
          {members.length > 0 ? (
            <div className="break-words pt-1 text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">
              Cycle: {members.map(shortFile).join(" → ")}
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  if (plan.refactoring_type === "split_file") {
    const groups = splitGroups(plan);
    const residual = splitResidual(plan);
    const shim = splitShimRequired(plan);
    const blast = splitBlast(plan);
    const originalName = plan.file_path.split("/").pop() ?? plan.file_path;
    const shownDeps = blast.dependent_files.slice(0, 8);
    return (
      <div className="space-y-3">
        {hideIntro ? null : (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Split into {groups.length} cohesive module{groups.length === 1 ? "" : "s"} — each
            group's symbols move to a new file
            {shim ? ", with a back-compat shim left in the original path" : ""}.
          </p>
        )}
        <div className="grid gap-2.5 sm:grid-cols-2">
          {groups.map((g, i) => (
            <div
              key={i}
              className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3.5"
            >
              <div className="mb-1 flex items-center gap-2">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: accent }}
                  aria-hidden
                />
                <span className="text-xs font-semibold text-[var(--color-text-primary)]">
                  {g.name ?? `Group ${i + 1}`}
                </span>
              </div>
              {g.suggested_file ? (
                <div className="mb-2 flex items-center gap-1.5">
                  <FilePlus2 className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
                  <FileRef path={g.suggested_file} fileHref={fileHref} className="min-w-0 truncate" />
                </div>
              ) : null}
              <ul className="space-y-0.5">
                {g.symbols.map((s) => (
                  <li
                    key={s}
                    className="flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-text-secondary)]"
                  >
                    <CornerDownRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
                    <span className="truncate">{s}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        {residual.length > 0 ? (
          <div className="rounded-xl border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3.5">
            <div className="mb-1.5 text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
              Stays in {originalName} (shared core)
            </div>
            <div className="flex flex-wrap gap-1">
              {residual.map((s) => (
                <code
                  key={s}
                  className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-secondary)]"
                >
                  {s}
                </code>
              ))}
            </div>
          </div>
        ) : null}

        {blast.dependent_count > 0 ? (
          <div className="space-y-1.5 rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3.5">
            <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
              <Layers className="h-3.5 w-3.5" />
              {blast.import_rewrites > 0
                ? `Rewrite imports in ${blast.import_rewrites} dependent file${
                    blast.import_rewrites === 1 ? "" : "s"
                  }`
                : `${blast.dependent_count} dependent${
                    blast.dependent_count === 1 ? "" : "s"
                  } · same-package split, no import edits`}
            </div>
            {blast.import_rewrites > 0 ? (
              <ul className="space-y-1">
                {shownDeps.map((f) => (
                  <li key={f}>
                    <FileRef path={f} fileHref={fileHref} className="block truncate" />
                  </li>
                ))}
                {blast.dependent_files.length > shownDeps.length ? (
                  <li className="text-[11px] text-[var(--color-text-tertiary)]">
                    +{blast.dependent_files.length - shownDeps.length} more
                  </li>
                ) : null}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  // Unknown type — show the raw blast files as a minimal fallback.
  const files = blastFiles(plan);
  return files.length ? (
    <ul className="space-y-1">
      {files.map((f) => (
        <li key={f}>
          <FileRef path={f} fileHref={fileHref} />
        </li>
      ))}
    </ul>
  ) : null;
}
