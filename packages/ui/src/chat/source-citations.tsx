"use client";

import { useMemo } from "react";
import {
  FileText,
  FolderOpen,
  Globe,
  Sparkles,
  LayoutGrid,
  ArrowUpRight,
} from "lucide-react";
import type { ChatUIToolCall } from "@repowise-dev/types/chat";

export interface SourceReference {
  id: string;
  pageId: string;
  title: string;
  pageType: string;
  targetPath: string;
  score?: number;
  toolName: string;
}

export function extractSources(
  toolCalls: ChatUIToolCall[],
  _repoId: string,
): SourceReference[] {
  const seen = new Set<string>();
  const sources: SourceReference[] = [];

  for (const tc of toolCalls) {
    if (tc.status !== "done" || !tc.result) continue;

    const result = tc.result;

    if (tc.name === "search_codebase") {
      const results = (result.results as Array<Record<string, unknown>>) ?? [];
      for (const r of results) {
        const pageId = r.page_id as string;
        if (!pageId || seen.has(pageId)) continue;
        seen.add(pageId);
        sources.push({
          id: `${tc.id}:${pageId}`,
          pageId,
          title: (r.title as string) ?? pageId,
          pageType: (r.page_type as string) ?? "file_page",
          targetPath: (r.target_path as string) ?? "",
          score: r.relevance_score as number ?? r.score as number,
          toolName: tc.name,
        });
      }
    }

    if (tc.name === "get_context") {
      const targets = result.targets as Record<string, Record<string, unknown>> | undefined;
      if (targets) {
        for (const [target, info] of Object.entries(targets)) {
          const docs = info.docs as Record<string, unknown> | undefined;
          if (!docs) continue;
          const pageId = (docs.page_id as string) ?? `file_page:${target}`;
          if (seen.has(pageId)) continue;
          seen.add(pageId);
          sources.push({
            id: `${tc.id}:${pageId}`,
            pageId,
            title: (docs.title as string) ?? target.split("/").pop() ?? target,
            pageType: (docs.page_type as string) ?? "file_page",
            targetPath: target,
            toolName: tc.name,
          });
        }
      }
    }

    if (tc.name === "get_overview") {
      const title = result.title as string;
      if (title) {
        const pageId = "repo_overview:";
        if (!seen.has(pageId)) {
          seen.add(pageId);
          sources.push({
            id: `${tc.id}:overview`,
            pageId,
            title: title ?? "Repository Overview",
            pageType: "repo_overview",
            targetPath: "",
            toolName: tc.name,
          });
        }
      }
    }

    if (tc.name === "get_why") {
      const decisions = (result.decisions as Array<Record<string, unknown>>)
        ?? (result.matching_decisions as Array<Record<string, unknown>>)
        ?? [];
      for (const d of decisions) {
        const affectedFiles = (d.affected_files as string[]) ?? [];
        for (const filePath of affectedFiles.slice(0, 3)) {
          const pageId = `file_page:${filePath}`;
          if (seen.has(pageId)) continue;
          seen.add(pageId);
          sources.push({
            id: `${tc.id}:${pageId}`,
            pageId,
            title: filePath.split("/").pop() ?? filePath,
            pageType: "file_page",
            targetPath: filePath,
            toolName: tc.name,
          });
        }
      }
    }

    if (tc.name === "get_architecture_diagram") {
      const pageId = "architecture_diagram:";
      if (!seen.has(pageId)) {
        seen.add(pageId);
        sources.push({
          id: `${tc.id}:arch-diagram`,
          pageId,
          title: "Architecture Diagram",
          pageType: "architecture_diagram",
          targetPath: "",
          toolName: tc.name,
        });
      }
    }
  }

  return sources;
}

const TYPE_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  repo_overview: Globe,
  architecture_diagram: LayoutGrid,
  module_page: FolderOpen,
  symbol_spotlight: Sparkles,
};

function SourceIcon({ pageType, className }: { pageType: string; className?: string }) {
  const Icon = TYPE_ICON[pageType] ?? FileText;
  return <Icon {...(className ? { className } : {})} />;
}

interface SourceCitationsProps {
  toolCalls: ChatUIToolCall[];
  repoId: string;
}

export function SourceCitations({ toolCalls, repoId }: SourceCitationsProps) {
  const sources = useMemo(
    () => extractSources(toolCalls, repoId),
    [toolCalls, repoId],
  );

  if (sources.length === 0) return null;

  return (
    <div className="mt-2 pt-2 border-t border-[var(--color-border-default)]/50">
      <p className="text-[10px] text-[var(--color-text-tertiary)] uppercase tracking-wider font-medium mb-1.5">
        Sources
      </p>
      <div className="flex flex-wrap gap-1.5">
        {sources.map((source, idx) => (
          <a
            key={source.id}
            href={`/repos/${repoId}/wiki/${encodeURIComponent(source.pageId)}`}
            className="group inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-[10px] text-[var(--color-text-secondary)] hover:border-[var(--color-accent-primary)] hover:text-[var(--color-accent-primary)] hover:bg-[var(--color-accent-muted)] transition-all"
          >
            <span className="flex items-center justify-center h-3.5 w-3.5 rounded-sm bg-[var(--color-bg-overlay)] text-[9px] font-bold text-[var(--color-text-tertiary)] group-hover:bg-[var(--color-accent-primary)] group-hover:text-white shrink-0 transition-colors">
              {idx + 1}
            </span>
            <SourceIcon pageType={source.pageType} className="h-3 w-3 shrink-0 opacity-60" />
            <span className="truncate max-w-[160px] font-medium">{source.title}</span>
            {source.score != null && (
              <span className="text-[9px] text-[var(--color-text-tertiary)] tabular-nums">
                {(source.score * 100).toFixed(0)}%
              </span>
            )}
            <ArrowUpRight className="h-2.5 w-2.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
          </a>
        ))}
      </div>
    </div>
  );
}
