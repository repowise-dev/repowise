import type { ReactNode } from "react";
import { BookOpen } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { Badge } from "../ui/badge";
import { formatRelativeTime } from "../lib/format";
import type { FileWikiPageRef } from "@repowise-dev/types/files";

interface FileDocTabProps {
  wikiPage: FileWikiPageRef | null;
  /** Server-rendered wiki content (the host renders markdown its own way). */
  docSlot?: ReactNode | undefined;
  /** Deep link into the docs reading surface. */
  wikiHref?: string | undefined;
}

const FRESHNESS_CLASS: Record<string, string> = {
  fresh: "text-[var(--color-success)] border-[var(--color-success)]/30",
  stale: "text-[var(--color-warning)] border-[var(--color-warning)]/30",
  outdated: "text-[var(--color-error)] border-[var(--color-error)]/30",
};

export function FileDocTab({ wikiPage, docSlot, wikiHref }: FileDocTabProps) {
  if (!wikiPage) {
    return (
      <EmptyState
        icon={<BookOpen className="h-8 w-8" />}
        title="No documentation yet"
        description="This file hasn't been documented. Run a sync to generate its wiki page."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant="outline"
          className={`text-[10px] h-5 ${FRESHNESS_CLASS[wikiPage.freshness_status] ?? ""}`}
        >
          {wikiPage.freshness_status}
        </Badge>
        {wikiPage.updated_at && (
          <span
            className="text-xs text-[var(--color-text-tertiary)]"
            title={new Date(wikiPage.updated_at).toLocaleString()}
          >
            updated {formatRelativeTime(wikiPage.updated_at)}
          </span>
        )}
        {wikiHref && (
          <a
            href={wikiHref}
            className="ml-auto text-xs text-[var(--color-accent-primary)] hover:underline"
          >
            Open in Docs →
          </a>
        )}
      </div>
      {wikiPage.human_notes && (
        <div className="rounded-md border border-[var(--color-accent-primary)]/30 bg-[var(--color-bg-elevated)] p-3">
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1">
            Team notes
          </p>
          <p className="text-xs text-[var(--color-text-secondary)] whitespace-pre-wrap">
            {wikiPage.human_notes}
          </p>
        </div>
      )}
      <article className="prose prose-invert max-w-none leading-relaxed overflow-hidden">
        {docSlot}
      </article>
    </div>
  );
}
