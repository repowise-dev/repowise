"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  FileText,
  Clock,
  Cpu,
  StickyNote,
  ArrowRight,
  ArrowLeft,
  Loader2,
  Network,
  Activity,
  GitBranch,
  Layers,
  Flame,
  FileInput,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { getGitMetadata } from "@/lib/api/git";
import { WikiMarkdown } from "@repowise-dev/ui/wiki/wiki-markdown";
import { getBacklinks, getWikiLinks } from "@repowise-dev/ui/wiki/wiki-links-types";
import { TableOfContents } from "@repowise-dev/ui/wiki/table-of-contents";
import { computeDocNav } from "@repowise-dev/ui/docs/doc-nav";
import { Breadcrumb } from "@repowise-dev/ui/shared/breadcrumb";
import { BacklinksPanel } from "@repowise-dev/ui/wiki/backlinks-panel";
import { getPageTypeLabel } from "@repowise-dev/ui/lib/page-types";
import {
  type ReaderPersona,
  filterMarkdownByPersona,
} from "@repowise-dev/ui/docs/reader-persona";
import { VersionHistoryWrapper } from "@/components/wiki/version-history";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { formatRelativeTime, formatTokens } from "@repowise-dev/ui/lib/format";
import { useGraphMetrics, useCallersCallees } from "@/lib/hooks/use-graph";
import type { PageResponse } from "@/lib/api/types";

function PercentileBar({ value, label }: { value: number; label: string }) {
  const pct = 100 - value;
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[11px] text-[var(--color-text-tertiary)] shrink-0">{label}</span>
      <div className="flex items-center gap-1.5">
        <div className="w-16 h-1.5 rounded-full bg-[var(--color-bg-elevated)] overflow-hidden">
          <div
            className={cn(
              "h-full rounded-full",
              value >= 75 ? "bg-green-400" : value >= 50 ? "bg-yellow-400" : "bg-[var(--color-text-tertiary)]",
            )}
            style={{ width: `${value}%` }}
          />
        </div>
        <span className={cn(
          "text-[10px] font-mono tabular-nums w-10 text-right",
          value >= 75 ? "text-green-400" : value >= 50 ? "text-yellow-400" : "text-[var(--color-text-tertiary)]",
        )}>
          Top {pct}%
        </span>
      </div>
    </div>
  );
}

function DocsSidebar({ repoId, targetPath }: { repoId: string; targetPath: string }) {
  const nodeId = targetPath;
  const { metrics, isLoading: metricsLoading } = useGraphMetrics(repoId, nodeId);
  const symbolNodeId = targetPath.includes("::") ? targetPath : null;
  const { data: callData } = useCallersCallees(repoId, symbolNodeId);

  if (metricsLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-3/4" />
      </div>
    );
  }

  // No graph data — render nothing rather than announcing the absence.
  if (!metrics) return null;

  return (
    <div className="space-y-4">
      {/* Graph Metrics */}
      <div>
        <div className="flex items-center gap-1.5 mb-2">
          <Activity className="h-3 w-3 text-[var(--color-text-tertiary)]" />
          <span className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
            Importance
          </span>
        </div>
        <div className="space-y-2">
          <PercentileBar value={metrics.pagerank_percentile} label="PageRank" />
          <PercentileBar value={metrics.betweenness_percentile} label="Centrality" />
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-[var(--color-text-tertiary)]">Degree</span>
            <span className="text-[11px] font-mono text-[var(--color-text-secondary)]">
              {metrics.in_degree} in &middot; {metrics.out_degree} out
            </span>
          </div>
          {metrics.is_entry_point && (
            <Badge variant="accent" className="text-[10px]">Entry Point</Badge>
          )}
        </div>
      </div>

      {/* Community */}
      {metrics.community_label && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <Network className="h-3 w-3 text-[var(--color-text-tertiary)]" />
            <span className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
              Community
            </span>
          </div>
          <Link
            href={`/repos/${repoId}/graph?colorMode=community`}
            className="inline-flex items-center gap-1 text-[11px] text-[var(--color-accent)] hover:underline"
          >
            {metrics.community_label}
            <ArrowRight className="h-2.5 w-2.5" />
          </Link>
        </div>
      )}

      {/* Callers/Callees (for symbol pages) */}
      {callData && (callData.caller_count > 0 || callData.callee_count > 0) && (
        <div>
          <div className="flex items-center gap-1.5 mb-2">
            <GitBranch className="h-3 w-3 text-[var(--color-text-tertiary)]" />
            <span className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
              Call Graph
            </span>
          </div>
          {callData.caller_count > 0 && (
            <div className="mb-2">
              <p className="text-[10px] text-[var(--color-text-tertiary)] mb-1">Called by ({callData.caller_count})</p>
              {callData.callers.slice(0, 5).map((c) => (
                <p key={c.symbol_id} className="text-[11px] font-mono text-[var(--color-text-secondary)] truncate pl-2" title={c.symbol_id}>
                  {c.name}
                </p>
              ))}
            </div>
          )}
          {callData.callee_count > 0 && (
            <div>
              <p className="text-[10px] text-[var(--color-text-tertiary)] mb-1">Calls ({callData.callee_count})</p>
              {callData.callees.slice(0, 5).map((c) => (
                <p key={c.symbol_id} className="text-[11px] font-mono text-[var(--color-text-secondary)] truncate pl-2" title={c.symbol_id}>
                  {c.name}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Consolidated "At a glance" signals for a file page — hotspot, ownership,
 * churn, bus factor — pulled from the existing git-metadata endpoint so the
 * reader doesn't have to leave the wiki to learn how risky/owned a file is.
 * Renders nothing for non-file pages or files without git history.
 */
function AtAGlance({ repoId, targetPath }: { repoId: string; targetPath: string }) {
  const isFile =
    !!targetPath &&
    !targetPath.includes("::") &&
    !targetPath.startsWith("onboarding/") &&
    !targetPath.startsWith("layer:");
  const { data } = useSWR(
    isFile ? `git-meta:${repoId}:${targetPath}` : null,
    () => getGitMetadata(repoId, targetPath),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  if (!data || data.commit_count_total === 0) return null;

  const churnTop = Math.max(1, 100 - Math.round(data.churn_percentile));
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-2">
        <Flame className="h-3 w-3 text-[var(--color-text-tertiary)]" />
        <span className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
          At a glance
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {data.is_hotspot && (
          <Badge variant="outline" className="text-[10px] border-[var(--color-warning)]/40 text-[var(--color-warning)]">
            <Flame className="h-2.5 w-2.5 mr-1" />
            Hotspot · top {churnTop}%
          </Badge>
        )}
        {data.is_stable && !data.is_hotspot && (
          <Badge variant="outline" className="text-[10px]">Stable</Badge>
        )}
        {data.bus_factor === 1 && (
          <Badge variant="outline" className="text-[10px] border-[var(--color-warning)]/40 text-[var(--color-warning)]">
            Bus factor 1
          </Badge>
        )}
      </div>
      <div className="mt-2 space-y-1 text-[11px] text-[var(--color-text-secondary)]">
        {data.primary_owner_name && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[var(--color-text-tertiary)]">Owner</span>
            <span className="truncate" title={data.primary_owner_name}>
              {data.primary_owner_name}
              {data.primary_owner_commit_pct != null &&
                ` · ${Math.round(data.primary_owner_commit_pct * 100)}%`}
            </span>
          </div>
        )}
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--color-text-tertiary)]">Commits (90d)</span>
          <span className="font-mono">{data.commit_count_90d}</span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--color-text-tertiary)]">Contributors</span>
          <span className="font-mono">{data.contributor_count}</span>
        </div>
        {data.agent_authored_pct != null && (data.agent_commit_count ?? 0) > 0 && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[var(--color-text-tertiary)]">Agent-authored</span>
            <span className="font-mono">
              {Math.round(data.agent_authored_pct * 100)}% · {data.agent_commit_count}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

interface DocsViewerProps {
  page: PageResponse | null;
  /** Full page list — powers hierarchical breadcrumbs and prev/next. */
  pages?: PageResponse[];
  repoId: string;
  isLoading?: boolean;
  /** Select another page in-place (breadcrumb / prev-next / wiki links). */
  onSelectPage?: (page: PageResponse) => void;
  /** Reader level + insights-drawer state — owned by DocsExplorer, which
      renders the controls in the DocsHeader row. */
  persona: ReaderPersona;
  sidebarOpen: boolean;
}

export function DocsViewer({
  page,
  pages = [],
  repoId,
  isLoading,
  onSelectPage,
  persona,
  sidebarOpen,
}: DocsViewerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // In-app navigation to another page by id (used by breadcrumbs, prev/next,
  // and resolved wiki links). Falls back to the full wiki route when the
  // target isn't in the loaded list or no handler was supplied.
  const goToPageId = useCallback(
    (pageId: string) => {
      const target = pages.find((p) => p.id === pageId);
      if (target && onSelectPage) onSelectPage(target);
      else
        window.location.href = `/repos/${repoId}/wiki/${encodeURIComponent(pageId)}`;
    },
    [pages, onSelectPage, repoId],
  );

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

  const hasTargetPath = !!page.target_path;

  return (
    <DocsViewerBody
      page={page}
      pages={pages}
      repoId={repoId}
      hasTargetPath={hasTargetPath}
      sidebarOpen={sidebarOpen}
      scrollRef={scrollRef}
      goToPageId={goToPageId}
      persona={persona}
    />
  );
}

function DocsViewerBody({
  page,
  pages,
  repoId,
  hasTargetPath,
  sidebarOpen,
  scrollRef,
  goToPageId,
  persona,
}: {
  page: PageResponse;
  pages: PageResponse[];
  repoId: string;
  hasTargetPath: boolean;
  sidebarOpen: boolean;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  goToPageId: (pageId: string) => void;
  persona: ReaderPersona;
}) {
  // Hierarchical breadcrumb + sibling prev/next, derived from the page list.
  const nav = useMemo(() => computeDocNav(page, pages), [page, pages]);
  const wikiLinks = useMemo(() => getWikiLinks(page.metadata), [page.metadata]);

  // Persona-filtered markdown — hides reference-heavy sections for the lighter
  // reading levels. The TOC + download still operate on the full content.
  const visibleContent = useMemo(
    () => filterMarkdownByPersona(page.content, persona),
    [page.content, persona],
  );

  // Nearest ancestor that maps to a module page — the "zoom out" chip target.
  const moduleSeg = useMemo(
    () =>
      [...nav.breadcrumbs]
        .slice(0, -1)
        .reverse()
        .find((s) => s.pageId && s.pageId !== page.id),
    [nav.breadcrumbs, page.id],
  );

  const buildWikiHref = useCallback(
    (pageId: string) =>
      `/repos/${repoId}/docs?page=${encodeURIComponent(pageId)}`,
    [repoId],
  );

  // Forward "Related" links: distinct wiki-link targets resolved to titles
  // from the loaded page list. Pure metadata — no extra request.
  const relatedLinks = useMemo(() => {
    const byId = new Map(pages.map((p) => [p.id, p]));
    const seen = new Set<string>();
    const out: { id: string; title: string }[] = [];
    for (const link of wikiLinks) {
      const target = link.target_page_id;
      if (target === page.id || seen.has(target)) continue;
      const hit = byId.get(target);
      if (!hit) continue;
      seen.add(target);
      out.push({ id: hit.id, title: hit.title });
    }
    return out;
  }, [wikiLinks, pages, page.id]);

  // KG layer this file belongs to (the "layer Y" chip). Links to the layer
  // page when one was generated, otherwise renders as a static chip.
  const layerName =
    typeof page.metadata?.layer_name === "string" ? page.metadata.layer_name : "";
  // Layer pages are keyed by the layer's STABLE slug id (`layer:<slug>`), which
  // survives the LLM rename of the display name. Join by that id; fall back to
  // the legacy name-keyed target_path for pages generated before the slug fix.
  const layerId =
    typeof page.metadata?.layer_id === "string" ? page.metadata.layer_id : "";
  const layerPage = useMemo(
    () =>
      layerId
        ? pages.find(
            (p) => p.page_type === "layer_page" && p.target_path === layerId,
          ) ??
          (layerName
            ? pages.find(
                (p) =>
                  p.page_type === "layer_page" &&
                  p.target_path === `layer:${layerName}`,
              )
            : undefined)
        : layerName
          ? pages.find(
              (p) =>
                p.page_type === "layer_page" &&
                p.target_path === `layer:${layerName}`,
            )
          : undefined,
    [pages, layerId, layerName],
  );

  // Provenance: the inputs this page was synthesised from (metadata.sources).
  // Each is linked to its own page when one exists.
  const sources = useMemo(() => {
    const raw = page.metadata?.sources;
    if (!Array.isArray(raw)) return [];
    const byPath = new Map(pages.map((p) => [p.target_path, p]));
    return (raw as Array<{ path?: string; kind?: string }>)
      .map((s) => {
        const path = typeof s?.path === "string" ? s.path : "";
        return { path, kind: s?.kind ?? "", pageId: byPath.get(path)?.id };
      })
      .filter((s) => s.path);
  }, [page.metadata, pages]);

  // Renders a resolved wiki link as an <a> with a real href (middle-click /
  // open-in-new-tab still work) but intercepts plain clicks for in-app nav.
  const WikiInlineLink = useMemo(() => {
    function Comp({
      href,
      className,
      title,
      children,
    }: {
      href: string;
      className?: string;
      title?: string;
      children: React.ReactNode;
    }) {
      return (
        <a
          href={href}
          className={className}
          title={title}
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
            try {
              const u = new URL(href, window.location.origin);
              const pid = u.searchParams.get("page");
              if (pid) {
                e.preventDefault();
                goToPageId(pid);
              }
            } catch {
              /* fall through to default navigation */
            }
          }}
        >
          {children}
        </a>
      );
    }
    return Comp;
  }, [goToPageId]);

  return (
    <div className="flex h-full">
      {/* Main content */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Content. The old sticky control row lives in the DocsHeader now —
            the article gets the full height. */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="px-4 sm:px-6 py-8 max-w-[768px] mx-auto">
            {/* Hierarchical breadcrumb: module / file, ancestors clickable */}
            <div className="mb-3 overflow-hidden">
              <Breadcrumb
                segments={nav.breadcrumbs.map((seg) => ({
                  label: seg.label,
                  ...(seg.pageId && seg.pageId !== page.id
                    ? { href: buildWikiHref(seg.pageId) }
                    : {}),
                }))}
                LinkComponent={WikiInlineLink}
              />
            </div>

            {/* Title */}
            <h1 className="font-serif text-[2rem] leading-tight font-semibold tracking-tight text-[var(--color-text-primary)] mb-2 break-words">
              {page.title}
            </h1>

            {/* Context chips: page type + "in module" (zoom-out) */}
            <div className="flex flex-wrap items-center gap-1.5 mb-2">
              <span className="rounded-full bg-[var(--color-bg-elevated)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                {getPageTypeLabel(page.page_type)}
              </span>
              {moduleSeg && (
                <button
                  onClick={() => goToPageId(moduleSeg.pageId!)}
                  className="rounded-full border border-[var(--color-border-default)] px-2 py-0.5 text-[10px] text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-accent)] hover:text-[var(--color-accent-primary)]"
                >
                  in {moduleSeg.label}
                </button>
              )}
              {layerName &&
                (layerPage ? (
                  <button
                    onClick={() => goToPageId(layerPage.id)}
                    className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border-default)] px-2 py-0.5 text-[10px] text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-accent)] hover:text-[var(--color-accent-primary)]"
                  >
                    <Layers className="h-2.5 w-2.5" />
                    {layerName}
                  </button>
                ) : (
                  <span className="inline-flex items-center gap-1 rounded-full bg-[var(--color-bg-elevated)] px-2 py-0.5 text-[10px] text-[var(--color-text-tertiary)]">
                    <Layers className="h-2.5 w-2.5" />
                    {layerName}
                  </span>
                ))}
            </div>

            {/* Low-confidence flag */}
            {page.confidence > 0 && page.confidence < 0.5 && (
              <div className="mb-4 flex items-start gap-1.5 rounded-md border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 px-3 py-2">
                <span className="text-xs text-[var(--color-text-primary)]">
                  This page was generated with low confidence — verify against the source before relying on it.
                </span>
              </div>
            )}

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
              {page.model_name && (
                <span className="flex items-center gap-1 font-mono">
                  <Cpu className="h-3 w-3" />
                  {page.model_name}
                </span>
              )}
            </div>

            {/* Human notes */}
            {page.human_notes && (
              <div className="mb-4 rounded-lg border border-[var(--color-border-accent)] bg-[var(--color-accent-blue)]/5 px-4 py-3">
                <div className="flex items-center gap-1.5 mb-1.5">
                  <StickyNote className="h-3.5 w-3.5 text-[var(--color-accent-blue)]" />
                  <span className="text-xs font-medium text-[var(--color-accent-blue)] uppercase tracking-wider">
                    Human Notes
                  </span>
                </div>
                <p className="text-sm text-[var(--color-text-secondary)] whitespace-pre-wrap leading-relaxed">
                  {page.human_notes}
                </p>
              </div>
            )}

            {/* Markdown content */}
            <article className="prose prose-invert max-w-none leading-relaxed overflow-hidden">
              <WikiMarkdown
                content={visibleContent}
                wikiLinks={wikiLinks}
                buildHref={(pid) => buildWikiHref(pid)}
                LinkComponent={WikiInlineLink}
              />
            </article>

            {/* Sibling prev / next */}
            {(nav.prev || nav.next) && (
              <nav className="mt-8 flex items-stretch gap-3 border-t border-[var(--color-border-default)] pt-4">
                {nav.prev ? (
                  <button
                    onClick={() => goToPageId(nav.prev!.pageId)}
                    className="group flex flex-1 items-center gap-2 rounded-lg border border-[var(--color-border-default)] px-3 py-2 text-left transition-colors hover:border-[var(--color-border-accent)] hover:bg-[var(--color-bg-elevated)]"
                  >
                    <ArrowLeft className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] group-hover:text-[var(--color-accent-primary)]" />
                    <span className="min-w-0">
                      <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                        Previous
                      </span>
                      <span className="block truncate text-xs text-[var(--color-text-secondary)]">
                        {nav.prev.title}
                      </span>
                    </span>
                  </button>
                ) : (
                  <span className="flex-1" />
                )}
                {nav.next && (
                  <button
                    onClick={() => goToPageId(nav.next!.pageId)}
                    className="group flex flex-1 items-center justify-end gap-2 rounded-lg border border-[var(--color-border-default)] px-3 py-2 text-right transition-colors hover:border-[var(--color-border-accent)] hover:bg-[var(--color-bg-elevated)]"
                  >
                    <span className="min-w-0">
                      <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                        Next
                      </span>
                      <span className="block truncate text-xs text-[var(--color-text-secondary)]">
                        {nav.next.title}
                      </span>
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] group-hover:text-[var(--color-accent-primary)]" />
                  </button>
                )}
              </nav>
            )}

            {/* Version history */}
            <div className="mt-8">
              <VersionHistoryWrapper
                pageId={page.id}
                currentVersion={page.version}
                currentContent={page.content}
              />
            </div>

            {/* Metadata warnings */}
            {Array.isArray(page.metadata?.hallucination_warnings) && (page.metadata.hallucination_warnings as string[]).length > 0 && (
              <div className="mt-4 rounded-lg border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 px-4 py-3">
                <p className="text-xs font-medium text-[var(--color-warning)] mb-1.5">
                  Possible inaccuracies detected
                </p>
                <ul className="space-y-0.5">
                  {(page.metadata.hallucination_warnings as string[]).map((w, i) => (
                    <li key={i} className="text-xs text-[var(--color-text-secondary)] font-mono">
                      {String(w)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Right sidebar — on-page contents first, then graph intelligence.
          Every section renders only when it has data, so the panel stays calm. */}
      {sidebarOpen && (
        <div className="hidden lg:block border-l border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shrink-0 w-[260px] overflow-auto">
          <div className="space-y-6 p-4">
            <TableOfContents content={page.content} />
            {hasTargetPath && (
              <AtAGlance repoId={repoId} targetPath={page.target_path} />
            )}
            {sources.length > 0 && (
              <div>
                <div className="flex items-center gap-1.5 mb-2">
                  <FileInput className="h-3 w-3 text-[var(--color-text-tertiary)]" />
                  <span className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                    Built from
                  </span>
                </div>
                <ul className="space-y-1">
                  {sources.slice(0, 5).map((s) => (
                    <li key={s.path} className="text-[11px]">
                      {s.pageId ? (
                        <button
                          onClick={() => goToPageId(s.pageId!)}
                          className="truncate text-left font-mono text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] transition-colors w-full"
                          title={`${s.path} (${s.kind})`}
                        >
                          {s.path.split("/").pop()}
                        </button>
                      ) : (
                        <span
                          className="block truncate font-mono text-[var(--color-text-tertiary)]"
                          title={`${s.path} (${s.kind})`}
                        >
                          {s.path.split("/").pop()}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
                {sources.length > 5 && (
                  <p className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">
                    + {sources.length - 5} more
                  </p>
                )}
              </div>
            )}
            {relatedLinks.length > 0 && (
              <div>
                <p className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
                  Related
                </p>
                <ul className="space-y-1.5">
                  {relatedLinks.slice(0, 5).map((r) => (
                    <li key={r.id} className="text-xs">
                      <button
                        onClick={() => goToPageId(r.id)}
                        className="truncate text-left text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] transition-colors w-full"
                        title={r.title}
                      >
                        {r.title}
                      </button>
                    </li>
                  ))}
                </ul>
                {relatedLinks.length > 5 && (
                  <p className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">
                    + {relatedLinks.length - 5} more
                  </p>
                )}
              </div>
            )}
            <BacklinksPanel
              backlinks={getBacklinks(page.metadata)}
              repoId={repoId}
              buildHref={(rid, pid) =>
                `/repos/${rid}/docs?page=${encodeURIComponent(pid)}`
              }
            />
            {hasTargetPath && <DocsSidebar repoId={repoId} targetPath={page.target_path} />}
          </div>
        </div>
      )}
    </div>
  );
}
