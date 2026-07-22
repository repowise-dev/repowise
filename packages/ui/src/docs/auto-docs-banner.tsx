"use client";

import { Sparkles, X } from "lucide-react";
import { formatNumber } from "../lib/format";
import { cn } from "../lib/cn";

/**
 * A thin, one-line strip shown above the docs reader when a repo still has
 * pages generated from structure (templates). It reframes an index-only wiki as
 * "auto-documented, upgrade me" rather than empty, and offers the bulk write,
 * without stealing vertical space from the reader.
 *
 * Presentational only: the caller counts template vs written pages, owns the
 * launch (`onWriteAll`), and owns dismissal (`onDismiss`) so it can persist
 * "don't show this again" per repo instead of nagging on every visit.
 */
export function AutoDocsBanner({
  templateCount,
  writtenCount,
  onWriteAll,
  onDismiss,
  className,
}: {
  /** Number of pages still rendered from templates (the upgrade target). */
  templateCount: number;
  /** Number of AI-written pages, used to word the "mixed" case honestly. */
  writtenCount: number;
  onWriteAll: () => void;
  /** Omit to hide the dismiss control (host owns whether it persists). */
  onDismiss?: () => void;
  className?: string;
}) {
  if (templateCount <= 0) return null;

  const mixed = writtenCount > 0;
  const pageWord = templateCount === 1 ? "page" : "pages";

  return (
    <div
      className={cn(
        "flex items-center gap-2 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-1.5 text-xs sm:px-6",
        className,
      )}
    >
      <Sparkles className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)]" />
      <span className="min-w-0 flex-1 truncate text-[var(--color-text-secondary)]">
        <span className="font-medium text-[var(--color-text-primary)]">
          {formatNumber(templateCount)}
        </span>{" "}
        {mixed ? `${pageWord} still` : pageWord} auto-documented from your code.
        Upgrade to AI-written prose for the how and why.
      </span>
      <button
        type="button"
        onClick={onWriteAll}
        className="shrink-0 font-medium text-[var(--color-accent-primary)] hover:text-[var(--color-accent-hover)]"
      >
        Write with AI
      </button>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 rounded p-0.5 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-inset)] hover:text-[var(--color-text-secondary)]"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
