/**
 * Sidebar panel that lists pages linking *into* the current page.
 *
 * Reads ``page.metadata.backlinks`` populated by the backend's
 * interlinking post-processor. Empty/missing metadata renders nothing
 * (graceful fallback for pages generated before interlinking shipped).
 */

import Link from "next/link";
import { getPageTypeIcon, getPageTypeLabel } from "../lib/page-types";
import type { Backlink } from "./wiki-links-types";

export interface BacklinksPanelProps {
  /** ``page.metadata.backlinks`` from the page response. */
  backlinks: Backlink[];
  /** Repo ID for routing the source-page links. */
  repoId: string;
  /** Maximum entries to show before "+N more". */
  limit?: number;
  /** Optional URL builder; defaults to ``/repos/{repoId}/wiki/{pageId}``. */
  buildHref?: (repoId: string, pageId: string) => string;
}

const DEFAULT_LIMIT = 6;

function defaultHref(repoId: string, pageId: string): string {
  // PageIds are like ``file_page:src/foo.py``. The router resolves them
  // via the catch-all slug parameter.
  return `/repos/${repoId}/wiki/${encodeURIComponent(pageId)}`;
}

export function BacklinksPanel({
  backlinks,
  repoId,
  limit = DEFAULT_LIMIT,
  buildHref = defaultHref,
}: BacklinksPanelProps) {
  if (!backlinks || backlinks.length === 0) {
    return null;
  }

  const visible = backlinks.slice(0, limit);
  const extra = backlinks.length - visible.length;

  return (
    <div>
      <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
        Linked by ({backlinks.length})
      </p>
      <ul className="space-y-1.5">
        {visible.map((bl) => {
          const Icon = getPageTypeIcon(bl.source_page_type);
          return (
            <li key={bl.source_page_id} className="text-xs">
              <Link
                href={buildHref(repoId, bl.source_page_id)}
                className="flex items-start gap-1.5 text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] transition-colors"
                title={getPageTypeLabel(bl.source_page_type)}
              >
                <Icon className="h-3 w-3 mt-0.5 shrink-0 text-[var(--color-text-tertiary)]" />
                <span className="truncate" title={bl.source_title}>
                  {bl.source_title}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
      {extra > 0 && (
        <p className="text-[10px] text-[var(--color-text-tertiary)] mt-1.5">
          + {extra} more
        </p>
      )}
    </div>
  );
}
