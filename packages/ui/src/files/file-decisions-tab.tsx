import * as React from "react";
import { Scale } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { stripMarkdown } from "../lib/format";
import type { GoverningDecisionRef } from "@repowise-dev/types/files";
import { FileSection } from "./file-section";

/** A decision's state, as a mono micro-label rather than a coloured pill.
 *  Only `deprecated` and `superseded` take ink, because those are the two that
 *  change how you should read the decision — rule 10, and rule 2 keeps the
 *  band colours for health. */
const STATUS_INK: Record<string, string> = {
  deprecated: "text-[var(--color-text-tertiary)] line-through",
  superseded: "text-[var(--color-text-tertiary)] line-through",
};

export interface FileDecisionsTabProps {
  decisions: GoverningDecisionRef[] | undefined | null;
  linkPrefix: string;
  LinkComponent?:
    | React.ElementType<{
        href: string;
        className?: string;
        children: React.ReactNode;
      }>
    | undefined;
}

export function FileDecisionsTab({
  decisions,
  linkPrefix,
  LinkComponent = "a",
}: FileDecisionsTabProps) {
  const items = decisions ?? [];
  const Link = LinkComponent;

  if (items.length === 0) {
    return (
      <EmptyState
        titleAs="h2"
        icon={<Scale className="h-8 w-8" />}
        title="No governing decisions"
        description="This file is not directly linked to any architectural governing decisions."
      />
    );
  }

  return (
    <FileSection
      first
      title="Governed by"
      description="Decisions recorded against this file. Open one to see what it settled and why."
    >
      {/* One verb per row — the whole row is the link, so there is no second
          "View decision →" control repeating it at the right edge. */}
      <ul className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
        {items.map((d) => (
          <li key={d.id}>
            <Link
              href={`${linkPrefix}/decisions/${d.id}`}
              className="-mx-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded px-2 py-3 transition-colors hover:bg-[var(--color-bg-elevated)]"
            >
              <span className="min-w-0 flex-1 text-sm font-medium text-[var(--color-text-primary)]">
                {stripMarkdown(d.title)}
              </span>
              <span
                className={`shrink-0 font-mono text-[10px] uppercase tracking-[0.12em] ${
                  STATUS_INK[d.status] ?? "text-[var(--color-text-tertiary)]"
                }`}
              >
                {d.status}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </FileSection>
  );
}
