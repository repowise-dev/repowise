import type { ReactNode } from "react";
import { BookOpen } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { formatDateTime, formatRelativeTime } from "../lib/format";
import type { FileWikiPageRef } from "@repowise-dev/types/files";

interface FileDocTabProps {
  wikiPage: FileWikiPageRef | null;
  /** Server-rendered wiki content (the host renders markdown its own way). */
  docSlot?: ReactNode | undefined;
}

export function FileDocTab({ wikiPage, docSlot }: FileDocTabProps) {
  if (!wikiPage) {
    return (
      <EmptyState
        titleAs="h2"
        icon={<BookOpen className="h-8 w-8" />}
        title="No documentation page for this file"
        description="Repowise writes pages for the files that carry a repository's shape. Re-run the index with a wider page budget to bring this one in."
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* The freshness marker lives in the header, where it sits beside the
          score and is on screen from whichever tab you arrive on. Repeating it
          here would be a badge on a row that already carries one. What is
          local to this tab is when the page was written.

          "Open in Documentation" used to sit here too, which made the one link
          out of this page reachable only from the tab that already renders the
          page. It is in the header now, so a reader on Health or Dependencies
          can see it. Leaving a second copy here would be two doors to one
          destination on one screen. */}
      {wikiPage.updated_at && (
        <p
          className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
          title={formatDateTime(wikiPage.updated_at)}
        >
          Written {formatRelativeTime(wikiPage.updated_at)}
        </p>
      )}

      {wikiPage.human_notes && (
        <div className="border-l-2 border-[var(--color-accent-primary)] pl-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            Team notes
          </p>
          <p className="mt-1.5 whitespace-pre-wrap text-sm leading-relaxed text-[var(--color-text-secondary)]">
            {wikiPage.human_notes}
          </p>
        </div>
      )}

      {/* No `prose` wrapper — rule 12. Tailwind Typography is banned on our
          markdown: every element the renderer emits is already themed through
          our tokens, `code::before/::after` prints literal backticks into the
          page, and `prose-invert` is a static class that cannot follow the
          theme. If the markdown needs a style, it gets styled in the renderer. */}
      <article className="max-w-none overflow-hidden">{docSlot}</article>
    </div>
  );
}
