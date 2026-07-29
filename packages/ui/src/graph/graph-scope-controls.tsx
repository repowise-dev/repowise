"use client";

import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { formatNumber } from "../lib/format";
import type { ModuleGroup } from "./use-module-filter";

/**
 * How zoomed out the graph is. Exactly one axis, in exactly one control.
 *
 * This used to be steered from two places at once — the page's tab strip
 * (`Communities | Explore | …`) and a floating pill cluster on the canvas
 * (`Communities | Modules | Full`). "Communities" appeared in both, and
 * "Explore" was a container name meaning "the graph, but not communities",
 * which is not a thing a reader thinks about. Tabs are datasets now; this is
 * the scope, and it follows the `MapLensSwitcher` precedent on Code Health:
 * labelled, in the section header, not floating over the diagram.
 */
export type GraphScope = "communities" | "files";

const SCOPES: { id: GraphScope; label: string; hint: string }[] = [
  {
    id: "communities",
    label: "Communities",
    hint: "One circle per detected community",
  },
  {
    id: "files",
    // Not "Full". It draws the most-connected slice of the repo, and the
    // caption under the tabs says exactly how big that slice is.
    label: "Files",
    hint: "Individual files and how they depend on each other",
  },
];

export function GraphScopeSwitcher({
  scope,
  onScopeChange,
  className,
}: {
  scope: GraphScope;
  onScopeChange: (scope: GraphScope) => void;
  className?: string;
}) {
  const onKeyDown = (e: ReactKeyboardEvent) => {
    const i = SCOPES.findIndex((s) => s.id === scope);
    if (i < 0) return;
    let next = i;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % SCOPES.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp")
      next = (i - 1 + SCOPES.length) % SCOPES.length;
    else return;
    e.preventDefault();
    const target = SCOPES[next];
    if (target) onScopeChange(target.id);
  };

  return (
    <div
      role="radiogroup"
      aria-label="Graph scope"
      onKeyDown={onKeyDown}
      className={`inline-flex rounded-md border border-[var(--color-border-default)] p-0.5 ${className ?? ""}`}
    >
      {SCOPES.map((s) => {
        const active = scope === s.id;
        return (
          <button
            key={s.id}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            title={s.hint}
            onClick={() => onScopeChange(s.id)}
            className={`rounded px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] ${
              active
                ? "bg-[var(--color-bg-elevated)] font-semibold text-[var(--color-text-primary)]"
                : "font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            }`}
          >
            {s.label}
          </button>
        );
      })}
    </div>
  );
}

/** Below this share of the graph a module is folded into the overflow list —
 *  it is still selectable, just not worth a slot in the front of the menu. */
const PROMINENT_MODULES = 8;

/**
 * Module filter for the Files scope: pick a path prefix, everything else dims.
 *
 * Deliberately absent from the Communities scope. The constellation payload
 * carries a community's label, size and one representative file — not its
 * members' paths — so "does this community contain `packages/ui`?" is a
 * question that view cannot answer. A control that could only guess is worse
 * than no control.
 */
export function ModuleFilterSelect({
  groups,
  activeModule,
  onModuleChange,
  className,
}: {
  groups: ModuleGroup[];
  activeModule: string | null;
  onModuleChange: (next: string | null) => void;
  className?: string;
}) {
  if (groups.length < 2) return null;

  const prominent = groups.slice(0, PROMINENT_MODULES);
  const rest = groups.slice(PROMINENT_MODULES);

  return (
    <label
      className={`inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)] ${className ?? ""}`}
    >
      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        Module
      </span>
      <select
        value={activeModule ?? ""}
        onChange={(e) => onModuleChange(e.target.value || null)}
        className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-xs text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        <option value="">All modules</option>
        {prominent.map((g) => (
          <option key={g.id} value={g.id}>
            {g.id} · {formatNumber(g.fileCount)}
          </option>
        ))}
        {rest.length > 0 && (
          <optgroup label="Smaller">
            {rest.map((g) => (
              <option key={g.id} value={g.id}>
                {g.id} · {formatNumber(g.fileCount)}
              </option>
            ))}
          </optgroup>
        )}
      </select>
    </label>
  );
}
