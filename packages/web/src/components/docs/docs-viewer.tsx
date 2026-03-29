"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import {
  FileText,
  Clock,
  Cpu,
  Hash,
  ExternalLink,
  Download,
  ArrowRight,
  RefreshCw,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { ChatMarkdown } from "@/components/chat/chat-markdown";
import { ConfidenceBadge } from "@/components/wiki/confidence-badge";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { formatRelativeTime, formatTokens } from "@/lib/utils/format";
import { downloadTextFile } from "@/lib/utils/download";
import type { PageResponse } from "@/lib/api/types";

interface DocsViewerProps {
  page: PageResponse | null;
  repoId: string;
  isLoading?: boolean;
}

export function DocsViewer({ page, repoId, isLoading }: DocsViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Scroll to top when page changes
  useEffect(() => {
    scrollRef.current?.scrollTo(0, 0);
  }, [page?.id]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-5 w-5 animate-spin text-[var(--color-accent-primary)]" />
      </div>
    );
  }

  if (!page) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
        <div className="rounded-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)] p-4">
          <FileText className="h-8 w-8 text-[var(--color-text-tertiary)]" />
        </div>
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Select a page
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)] max-w-sm">
            Choose a file or module from the tree to view its AI-generated documentation.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Sticky header */}
      <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)]/95 backdrop-blur px-4 sm:px-6 py-2.5 flex-wrap sm:flex-nowrap shrink-0">
        {/* Path breadcrumb */}
        <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)] min-w-0 flex-1">
          <span className="font-mono truncate text-[var(--color-text-secondary)]">
            {page.target_path || page.page_type}
          </span>
        </div>

        {/* Confidence */}
        <ConfidenceBadge
          score={page.confidence}
          status={page.freshness_status}
          showScore
        />

        {/* Provider */}
        <Badge variant="outline" className="font-mono text-[10px] hidden sm:flex shrink-0">
          <Cpu className="h-2.5 w-2.5 mr-1" />
          <span className="truncate max-w-[100px]">{page.model_name}</span>
        </Badge>

        {/* Download as markdown */}
        <button
          onClick={() => {
            const filename = (page.target_path || page.title).replace(/\//g, "_") + ".md";
            const header = `# ${page.title}\n\n> Path: ${page.target_path}\n\n`;
            downloadTextFile(header + page.content, filename);
          }}
          className="text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)] transition-colors shrink-0"
          title="Download as Markdown"
        >
          <Download className="h-3.5 w-3.5" />
        </button>

        {/* Open full page link */}
        <Link
          href={`/repos/${repoId}/wiki/${encodeURIComponent(page.id)}`}
          className="text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)] transition-colors shrink-0"
          title="Open full page"
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </Link>
      </div>

      {/* Content */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="px-4 sm:px-6 py-6 max-w-[768px] mx-auto">
          {/* Title */}
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 break-words">
            {page.title}
          </h1>

          {/* Meta row */}
          <div className="flex items-center gap-3 text-[10px] text-[var(--color-text-tertiary)] mb-6">
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {formatRelativeTime(page.updated_at)}
            </span>
            <span>v{page.version}</span>
            <span className="font-mono">
              {formatTokens(page.input_tokens)} in · {formatTokens(page.output_tokens)} out
            </span>
          </div>

          {/* Markdown content */}
          <article className="prose-chat">
            <ChatMarkdown content={page.content} />
          </article>
        </div>
      </div>
    </div>
  );
}
