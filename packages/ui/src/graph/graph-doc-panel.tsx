"use client";

import { useEffect, useRef } from "react";
import {
  X,
  FileText,
  ExternalLink,
  Clock,
  Cpu,
  AlertCircle,
} from "lucide-react";
import { Spinner } from "../ui/spinner";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { Markdown } from "../shared/markdown";
import { ConfidenceBadge } from "../wiki/confidence-badge";
import { formatRelativeTime } from "../lib/format";
import type { DocPage } from "@repowise-dev/types/docs";

export interface GraphDocPanelProps {
  /** Node id whose docs are being shown. Used in the header subtitle. */
  nodeId: string;
  /** Pre-fetched doc page; `null`/`undefined` while loading or when missing. */
  page: DocPage | null | undefined;
  /** Loading flag from the consumer's data hook. */
  isLoading: boolean;
  /** Truthy when no doc page exists for this node. */
  error?: unknown;
  /** Href for the file's own page. Also the empty state's way out: that page
   *  renders history, health, symbols and graph position for a file with no
   *  written documentation, so it is a real destination rather than a
   *  consolation link. */
  fullPageHref?: string;
  /** Href for the docs reading surface, offered when there is no file page. */
  browseDocsHref?: string;
  onClose: () => void;
}

export function GraphDocPanel({
  nodeId,
  page,
  isLoading,
  error,
  fullPageHref,
  browseDocsHref,
  onClose,
}: GraphDocPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo?.(0, 0);
  }, [nodeId]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border-default)] shrink-0">
        <FileText className="h-4 w-4 text-[var(--color-accent-primary)] shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-[var(--color-text-primary)] truncate">
            {page?.title ?? nodeId.split("/").pop()}
          </p>
          <p className="text-[10px] text-[var(--color-text-tertiary)] font-mono truncate">
            {nodeId}
          </p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {page && fullPageHref && (
            <a
              href={fullPageHref}
              className="text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)] transition-colors p-1"
              title="Open full page"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
            aria-label="Close documentation"
            className="h-6 w-6 p-0"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Meta bar */}
      {page && (
        <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--color-border-default)] shrink-0 flex-wrap">
          <ConfidenceBadge
            score={page.confidence}
            status={page.freshness_status}
            showScore
          />
          <Badge variant="outline" className="font-mono text-[10px]">
            <Cpu className="h-2.5 w-2.5 mr-0.5" />
            {page.model_name}
          </Badge>
          <span className="text-[10px] text-[var(--color-text-tertiary)] flex items-center gap-1 ml-auto">
            <Clock className="h-2.5 w-2.5" />
            {formatRelativeTime(page.updated_at)}
          </span>
        </div>
      )}

      {/* Content */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {isLoading && (
          <div className="flex items-center justify-center h-32">
            <Spinner size="lg" label="Loading documentation" className="text-[var(--color-accent-primary)]" />
          </div>
        )}

        {Boolean(error) && !isLoading && (
          <div className="flex flex-col items-center justify-center h-32 gap-2 px-6 text-center">
            <AlertCircle className="h-5 w-5 text-[var(--color-text-tertiary)]" />
            {/* Same words as the file page's own empty state
                (`files/file-doc-tab.tsx`), because one absence should not have
                two vocabularies. "Not found" also read as a failure; a file
                with no extracted symbols that is neither an entry point nor a
                hotspot is deliberately not given a page. */}
            <p className="text-xs font-medium text-[var(--color-text-secondary)]">
              No documentation page for this file
            </p>
            <p className="text-[11px] text-[var(--color-text-tertiary)]">
              Repowise writes pages for the files that carry a repository&apos;s
              shape. Everything else it knows about this one is still on the
              file&apos;s own page.
            </p>
            {fullPageHref ? (
              <a
                href={fullPageHref}
                className="text-xs text-[var(--color-accent-primary)] hover:underline"
              >
                Open the file page
              </a>
            ) : (
              browseDocsHref && (
                <a
                  href={browseDocsHref}
                  className="text-xs text-[var(--color-accent-primary)] hover:underline"
                >
                  Browse all docs
                </a>
              )
            )}
          </div>
        )}

        {page && !isLoading && (
          <div className="px-4 py-4">
            <article>
              <Markdown content={page.content} density="compact" />
            </article>
          </div>
        )}
      </div>
    </div>
  );
}
