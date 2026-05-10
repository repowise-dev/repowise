"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Compass } from "lucide-react";

export interface RelatedSection {
  id: string;
  label: string;
  /** Optional count shown as a chip in the header. */
  count?: number;
  /** Render the body. Lazy so collapsed sections don't pay render cost. */
  render: () => React.ReactNode;
}

export interface RelatedAcrossRepowiseProps {
  sections: RelatedSection[];
  /** Defaults to "Related across Repowise". */
  title?: string;
  /** Sections to start expanded. Defaults to none. */
  defaultExpanded?: string[];
}

/**
 * Universal footer attached to every entity page (file, symbol, decision,
 * wiki). Renders only sections that the host actually populates — sections
 * whose `render()` returns null are filtered out before rendering.
 */
export function RelatedAcrossRepowise({
  sections,
  title = "Related across Repowise",
  defaultExpanded = [],
}: RelatedAcrossRepowiseProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set(defaultExpanded));

  if (sections.length === 0) return null;

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <section className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
      <header className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border-default)]">
        <Compass className="h-4 w-4 text-[var(--color-text-tertiary)]" />
        <h2 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          {title}
        </h2>
      </header>
      <ul className="divide-y divide-[var(--color-border-default)]">
        {sections.map((s) => {
          const isOpen = expanded.has(s.id);
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => toggle(s.id)}
                aria-expanded={isOpen}
                className="w-full flex items-center gap-2 px-4 py-2.5 text-left hover:bg-[var(--color-bg-elevated)] transition-colors"
              >
                {isOpen ? (
                  <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-tertiary)] shrink-0" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-tertiary)] shrink-0" />
                )}
                <span className="text-sm text-[var(--color-text-primary)] flex-1">{s.label}</span>
                {s.count !== undefined && s.count > 0 && (
                  <span className="rounded-full bg-[var(--color-bg-elevated)] px-2 py-0.5 text-[10px] tabular-nums text-[var(--color-text-secondary)]">
                    {s.count}
                  </span>
                )}
              </button>
              {isOpen && (
                <div className="px-4 pb-3 pt-1 text-sm text-[var(--color-text-secondary)]">
                  {s.render()}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
