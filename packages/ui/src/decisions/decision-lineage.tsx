"use client";

import * as React from "react";
import { ArrowDown } from "lucide-react";
import { Badge } from "../ui/badge";
import type { DecisionLineageEntry } from "@repowise-dev/types/decisions";

const STATUS_COLORS: Record<string, string> = {
  active: "bg-[var(--color-success)]",
  proposed: "bg-[var(--color-info)]",
  deprecated: "bg-[var(--color-error)]",
  superseded: "bg-[var(--color-text-tertiary)]",
};

const STATUS_BADGE_VARIANT: Record<string, string> = {
  active: "text-[var(--color-success)] border-[var(--color-success)]/30",
  proposed: "text-[var(--color-info)] border-[var(--color-info)]/30",
  deprecated: "text-[var(--color-error)] border-[var(--color-error)]/30",
  superseded: "text-[var(--color-text-tertiary)] border-[var(--color-text-tertiary)]/30",
};

/** Maps the stored relation verb to a human connector label. */
const RELATION_LABEL: Record<string, string> = {
  supersedes: "superseded by",
  refines: "refined by",
};

type LinkComponent = React.ElementType<{
  href: string;
  className?: string;
  children: React.ReactNode;
}>;

export interface DecisionLineageProps {
  /** Supersession chain, ordered root -> current. */
  lineage: DecisionLineageEntry[];
  repoId: string;
  linkPrefix?: string;
  LinkComponent?: LinkComponent;
}

/**
 * Vertical evolution timeline for a decision's supersession chain. Renders the
 * root decision at the top down to the current one, with a relation connector
 * ("superseded by" / "refined by") between each hop. Renders nothing for a
 * trivial chain (<= 1 entry).
 */
export function DecisionLineage({
  lineage,
  repoId,
  linkPrefix,
  LinkComponent = "a",
}: DecisionLineageProps) {
  if (!lineage || lineage.length <= 1) return null;

  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const Link = LinkComponent;

  return (
    <div className="rounded-lg border border-[var(--color-border-default)] p-4">
      <h3 className="mb-3 text-sm font-medium text-[var(--color-text-secondary)]">
        Evolution
      </h3>
      <ol className="space-y-0">
        {lineage.map((entry, i) => {
          const isLast = i === lineage.length - 1;
          const relationLabel = entry.relation
            ? RELATION_LABEL[entry.relation] ?? entry.relation
            : null;
          return (
            <li key={entry.id}>
              <div className="flex items-start gap-3">
                <span
                  className={`mt-1 h-[11px] w-[11px] shrink-0 rounded-full ring-2 ring-[var(--color-bg-surface)] ${
                    STATUS_COLORS[entry.status] ?? STATUS_COLORS.active
                  }`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Link
                      href={`${prefix}/decisions/${entry.id}`}
                      className="truncate text-sm font-medium text-[var(--color-accent-primary)] hover:underline"
                    >
                      {entry.title}
                    </Link>
                    <Badge
                      variant="outline"
                      className={`h-4 shrink-0 text-[10px] ${STATUS_BADGE_VARIANT[entry.status] ?? ""}`}
                    >
                      {entry.status}
                    </Badge>
                  </div>
                  {entry.source && (
                    <span className="text-[10px] text-[var(--color-text-tertiary)]">
                      {entry.source.replace(/_/g, " ")}
                    </span>
                  )}
                </div>
              </div>
              {!isLast && (
                <div className="ml-[5px] flex items-center gap-2 border-l border-[var(--color-border-default)] py-1.5 pl-[18px] text-xs text-[var(--color-text-tertiary)]">
                  <ArrowDown className="h-3 w-3" aria-hidden />
                  {relationLabel && <span>{relationLabel}</span>}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
