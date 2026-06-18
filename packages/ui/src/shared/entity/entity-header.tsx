import * as React from "react";
import { cn } from "../../lib/cn";
import { Breadcrumb, type BreadcrumbSegment } from "../breadcrumb";

export type EntityEyebrow = "FILE" | "SYMBOL" | "MODULE";

export interface EntityHeaderProps {
  eyebrow: EntityEyebrow;
  breadcrumb: BreadcrumbSegment[];
  /** Mono filename / kind-aware identity line. */
  identity: React.ReactNode;
  /** One-line "what is this" — wiki summary or signature fallback. */
  summary?: string;
  /** The single most important fact (health gauge / hotspot), aligned right. */
  primarySignal?: React.ReactNode;
  /** De-piled secondary badges row. */
  metaBadges?: React.ReactNode;
  /** Top exported symbols as chips. */
  keySymbols?: React.ReactNode;
  /** Owner avatar + bus-factor pill. */
  people?: React.ReactNode;
  /** Open in Docs / Show in graph / View symbols. */
  actions?: React.ReactNode;
  /** Governing decision records, grouped beneath. */
  decisions?: React.ReactNode;
  LinkComponent?: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  className?: string;
}

/**
 * The shared detail-page header: eyebrow → breadcrumb → identity + primary
 * signal → summary → meta badges → key symbols → people → actions → decisions.
 * Calm and airy; reused by file/symbol/module detail pages.
 */
export function EntityHeader({
  eyebrow,
  breadcrumb,
  identity,
  summary,
  primarySignal,
  metaBadges,
  keySymbols,
  people,
  actions,
  decisions,
  LinkComponent,
  className,
}: EntityHeaderProps) {
  return (
    <header className={cn("space-y-3", className)}>
      <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {eyebrow}
      </p>

      {breadcrumb.length > 0 && (
        <Breadcrumb
          segments={breadcrumb}
          {...(LinkComponent ? { LinkComponent } : {})}
        />
      )}

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 text-lg font-semibold text-[var(--color-text-primary)]">
          {identity}
        </div>
        {primarySignal && <div className="shrink-0">{primarySignal}</div>}
      </div>

      {summary && (
        <p className="text-sm text-[var(--color-text-secondary)]">{summary}</p>
      )}

      {metaBadges && (
        <div className="flex flex-wrap items-center gap-2">{metaBadges}</div>
      )}

      {keySymbols && (
        <div className="flex flex-wrap items-center gap-1.5">{keySymbols}</div>
      )}

      {people && <div className="flex items-center gap-2">{people}</div>}

      {actions && (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      )}

      {decisions && <div className="space-y-2">{decisions}</div>}
    </header>
  );
}
