"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import {
  X,
  FileText,
  ExternalLink,
  Clock,
  Cpu,
  Loader2,
  AlertCircle,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { Button } from "@repowise/ui/ui/button";
import { Badge } from "@repowise/ui/ui/badge";
import { ChatMarkdown } from "@/components/chat/chat-markdown";
import { ConfidenceBadge } from "@/components/wiki/confidence-badge";
import { usePage } from "@/lib/hooks/use-page";
import { formatRelativeTime } from "@repowise/ui/lib/format";

interface GraphDocPanelProps {
  repoId: string;
  nodeId: string;
  onClose: () => void;
}

export function GraphDocPanel({ repoId, nodeId, onClose }: GraphDocPanelProps) {
  const pageId = `file_page:${nodeId}`;
  const { page, isLoading, error } = usePage(pageId);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, 0);
  }, [nodeId]);

  return (
    <div className="absolute top-0 right-0 z-20 h-full w-[min(400px,calc(100vw-1.5rem))] flex flex-col bg-[var(--color-bg-surface)] border-l border-[var(--color-border-default)] shadow-2xl shadow-black/30">
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
          {page && (
            <Link
              href={`/repos/${repoId}/wiki/${encodeURIComponent(page.id)}`}
              className="text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)] transition-colors p-1"
              title="Open full page"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </Link>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
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
            <Loader2 className="h-5 w-5 animate-spin text-[var(--color-accent-primary)]" />
          </div>
        )}

        {error && !isLoading && (
          <div className="flex flex-col items-center justify-center h-32 gap-2 px-6 text-center">
            <AlertCircle className="h-5 w-5 text-[var(--color-text-tertiary)]" />
            <p className="text-xs text-[var(--color-text-tertiary)]">
              No documentation found for this file.
            </p>
            <Link
              href={`/repos/${repoId}/docs`}
              className="text-xs text-[var(--color-accent-primary)] hover:underline"
            >
              Browse all docs
            </Link>
          </div>
        )}

        {page && !isLoading && (
          <div className="px-4 py-4">
            <article className="prose-chat text-xs">
              <ChatMarkdown content={page.content} />
            </article>
          </div>
        )}
      </div>
    </div>
  );
}
