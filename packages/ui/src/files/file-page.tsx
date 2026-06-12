"use client";

import type { ReactNode } from "react";
import { Flame, DoorOpen, Trash2 } from "lucide-react";
import { Badge } from "../ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";
import { scoreBadgeClass } from "../health/tokens";
import { fileEntityPath, symbolEntityPath } from "../shared/entity/routes";
import { FileDocTab } from "./file-doc-tab";
import { FileHealthTab, type FindingStatus } from "./file-health-tab";
import { FileHistoryTab } from "./file-history-tab";
import { FileCoverageTab } from "./file-coverage-tab";
import { FileGraphTab } from "./file-graph-tab";
import type { FileDetailResponse } from "@repowise-dev/types/files";
import { FILE_PAGE_TABS, type FilePageTab } from "./file-page-tabs";

export interface FilePageProps {
  data: FileDetailResponse;
  repoId: string;
  linkPrefix?: string;
  /** Server-rendered wiki content for the Doc tab. */
  docSlot?: ReactNode;
  /** Shiki HTML with per-line data-covered attributes (Coverage tab). */
  coverageCodeHtml?: string;
  /** Deep link into the docs reading surface. */
  wikiHref?: string;
  initialTab?: FilePageTab;
  onTabChange?: (tab: FilePageTab) => void;
  onFindingStatusChange?: (findingId: string, status: FindingStatus) => Promise<void> | void;
}

/**
 * The canonical "everything about this file" page. Header (path, owner,
 * health, signal chips, governing decisions) + Doc / Health / History /
 * Coverage / Graph tabs. Purely presentational — data and callbacks come
 * from the host; routing is plain hrefs built off `linkPrefix`.
 */
export function FilePage({
  data,
  repoId,
  linkPrefix,
  docSlot,
  coverageCodeHtml,
  wikiHref,
  initialTab,
  onTabChange,
  onFindingStatusChange,
}: FilePageProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const score = data.health.metric?.score;
  const language = data.graph?.language;
  const owner = data.git?.primary_owner ?? null;
  const deadLines = data.dead_code.reduce((s, f) => s + f.lines, 0);
  const fileHref = (p: string) => fileEntityPath(prefix, p);
  const symbolHref = (s: string) => symbolEntityPath(prefix, s);

  const defaultTab: FilePageTab =
    initialTab && FILE_PAGE_TABS.includes(initialTab) ? initialTab : "doc";

  return (
    <div className="space-y-4">
      {/* ── Header ── */}
      <div className="space-y-2">
        <h1
          className="font-mono text-base sm:text-lg text-[var(--color-text-primary)] break-all"
          title={data.file_path}
        >
          {data.file_path}
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {score != null && (
            <span
              className={`inline-flex items-baseline rounded px-2 py-0.5 text-sm font-bold tabular-nums ${scoreBadgeClass(score)}`}
            >
              {score.toFixed(1)}
              <span className="ml-0.5 text-[10px] font-normal opacity-70">/10</span>
            </span>
          )}
          {language && (
            <Badge variant="outline" className="text-[10px] h-5 capitalize">
              {language}
            </Badge>
          )}
          {data.graph?.is_entry_point && (
            <Badge variant="outline" className="text-[10px] h-5 text-[var(--color-info)] border-[var(--color-info)]/30">
              <DoorOpen className="h-2.5 w-2.5" /> entry point
            </Badge>
          )}
          {data.git?.is_hotspot && (
            <Badge variant="outline" className="text-[10px] h-5 text-[var(--color-error)] border-[var(--color-error)]/30">
              <Flame className="h-2.5 w-2.5" /> hotspot
            </Badge>
          )}
          {data.dead_code.length > 0 && (
            <Badge variant="outline" className="text-[10px] h-5 text-[var(--color-warning)] border-[var(--color-warning)]/30">
              <Trash2 className="h-2.5 w-2.5" /> {data.dead_code.length} dead ({deadLines} lines)
            </Badge>
          )}
          {owner && (
            <a
              href={`${prefix}/owners/${encodeURIComponent(owner)}`}
              className="text-[11px] text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:underline"
            >
              owned by <span className="font-medium">{owner}</span>
              {data.git?.primary_owner_commit_pct != null &&
                ` (${Math.round(data.git.primary_owner_commit_pct * 100)}%)`}
            </a>
          )}
        </div>
        {data.governing_decisions.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Governed by
            </span>
            {data.governing_decisions.map((d) => (
              <a
                key={d.id}
                href={`${prefix}/decisions/${d.id}`}
                className="rounded border border-[var(--color-border-default)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:border-[var(--color-accent-primary)] transition-colors"
              >
                {d.title}
              </a>
            ))}
          </div>
        )}
      </div>

      {/* ── Tabs ── */}
      <Tabs defaultValue={defaultTab} onValueChange={(v) => onTabChange?.(v as FilePageTab)}>
        <TabsList className="flex-wrap">
          <TabsTrigger value="doc">Doc</TabsTrigger>
          <TabsTrigger value="health">
            Health
            {data.health.findings.length > 0 && (
              <span className="ml-1 text-[10px] tabular-nums opacity-70">
                {data.health.findings.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
          <TabsTrigger value="coverage">Coverage</TabsTrigger>
          <TabsTrigger value="graph">Graph</TabsTrigger>
        </TabsList>
        <TabsContent value="doc" className="mt-4">
          <FileDocTab wikiPage={data.wiki_page} docSlot={docSlot} wikiHref={wikiHref} />
        </TabsContent>
        <TabsContent value="health" className="mt-4">
          <FileHealthTab
            health={data.health}
            functionBlame={data.function_blame}
            onFindingStatusChange={onFindingStatusChange}
            partnerHref={fileHref}
            symbolHref={symbolHref}
          />
        </TabsContent>
        <TabsContent value="history" className="mt-4">
          <FileHistoryTab git={data.git} linkPrefix={prefix} partnerHref={fileHref} />
        </TabsContent>
        <TabsContent value="coverage" className="mt-4">
          <FileCoverageTab coverage={data.coverage} coverageCodeHtml={coverageCodeHtml} />
        </TabsContent>
        <TabsContent value="graph" className="mt-4">
          <FileGraphTab
            graph={data.graph}
            filePath={data.file_path}
            linkPrefix={prefix}
            fileHref={fileHref}
            symbolHref={symbolHref}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
