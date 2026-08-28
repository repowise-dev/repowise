"use client";

import { useMemo } from "react";
import { ArrowUpRight } from "lucide-react";
import { getPageTypeIcon } from "../lib/page-types";
import { filePageId } from "../shared/entity/routes";
import type { ChatUIToolCall } from "@repowise-dev/types/chat";

export interface SourceReference {
  id: string;
  pageId: string;
  title: string;
  pageType: string;
  targetPath: string;
  /**
   * Rank-normalized 0–1 confidence, never the raw backend score: embedding
   * hits are cosine (0–1) but the BM25 fallback is unbounded, so the raw
   * score has no fixed scale to render as a percentage.
   */
  confidence?: number | undefined;
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
        // `page_id` is `${page_type}:${target_path}`, and the tool now omits it
        // wherever those two rebuild it. Derive rather than skip: the previous
        // `const pageId = r.page_id` dropped the whole row — and its citation —
        // for any result without one. Same fallback the get_context branch uses.
        const pageType = r.page_type as string | undefined;
        const targetPath = r.target_path as string | undefined;
        const pageId =
          (r.page_id as string | undefined) ??
          (pageType && targetPath ? `${pageType}:${targetPath}` : "");
        if (!pageId || seen.has(pageId)) continue;
        seen.add(pageId);
        sources.push({
          id: `${tc.id}:${pageId}`,
          pageId,
          title: (r.title as string) ?? pageId,
          pageType: (r.page_type as string) ?? "file_page",
          targetPath: (r.target_path as string) ?? "",
          confidence: r.confidence_score as number | undefined,
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
          const pageId = (docs.page_id as string) ?? filePageId(target);
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
      const stale = (result.stale_decisions as Array<Record<string, unknown>>) ?? [];
      const rows = [...decisions, ...stale];
      for (const d of rows) {
        const affectedFiles = (d.affected_files as string[]) ?? [];
        for (const filePath of affectedFiles.slice(0, 3)) {
          const pageId = filePageId(filePath);
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
  }

  return sources;
}

function SourceIcon({ pageType, className }: { pageType: string; className?: string }) {
  const Icon = getPageTypeIcon(pageType);
  return <Icon {...(className ? { className } : {})} />;
}

interface SourceCitationsProps {
  toolCalls: ChatUIToolCall[];
  repoId: string;
  linkPrefix?: string;
  /**
   * Builds the navigation URL for a citation. Defaults to
   * `{linkPrefix}/wiki/{pageId}` (or `/repos/{repoId}/wiki/{pageId}`).
   * Hosted-frontend can supply its own or just pass `linkPrefix`.
   */
  buildHref?: (source: SourceReference) => string;
}

const defaultBuildHref = (s: SourceReference, prefix: string) =>
  `${prefix}/wiki/${encodeURIComponent(s.pageId)}`;

export function SourceCitations({
  toolCalls,
  repoId,
  linkPrefix,
  buildHref,
}: SourceCitationsProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const sources = useMemo(
    () => extractSources(toolCalls, repoId),
    [toolCalls, repoId],
  );

  if (sources.length === 0) return null;

  return (
    <details className="group mt-5 border-t border-[var(--color-border-default)] pt-3">
      <summary className="flex min-h-8 cursor-pointer list-none items-center gap-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] marker:hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] [&::-webkit-details-marker]:hidden">
        <span>Sources · {sources.length}</span>
        <span aria-hidden className="transition-transform group-open:rotate-90">›</span>
      </summary>
      {/* Links, not chips. Each one goes somewhere, so the accent is earned on
          hover; a border and a ground on every entry turned a list of eight
          into a wall of boxes that outweighed the answer above it. The counter
          badge went with them — numbering is only worth its weight when the
          prose cites [1], and a reply does not. */}
      <ul className="flex flex-col gap-1.5 pb-1 pt-2">
        {sources.map((source) => (
          <li key={source.id}>
            <a
              href={buildHref ? buildHref(source) : defaultBuildHref(source, prefix)}
              className="group flex w-full min-w-0 items-baseline gap-2 text-[13px] text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] transition-colors"
            >
              <SourceIcon
                pageType={source.pageType}
                className="h-3.5 w-3.5 shrink-0 self-center text-[var(--color-text-tertiary)] group-hover:text-[var(--color-accent-primary)] transition-colors"
              />
              {/* No truncation: a cut title reports a layout decision to the
                  reader as missing content. Long ones wrap. */}
              <span className="min-w-0 font-medium [overflow-wrap:anywhere]">{source.title}</span>
              {source.confidence != null && (
                <span className="font-mono text-[11px] text-[var(--color-text-tertiary)] tabular-nums">
                  {(source.confidence * 100).toFixed(0)}%
                </span>
              )}
              <ArrowUpRight className="h-3 w-3 shrink-0 self-center opacity-0 group-hover:opacity-100 transition-opacity" />
            </a>
          </li>
        ))}
      </ul>
    </details>
  );
}
