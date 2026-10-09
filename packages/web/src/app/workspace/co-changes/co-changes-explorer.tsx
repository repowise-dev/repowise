"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { useTranslations } from "next-intl";
import { Search, X } from "lucide-react";
import type { GitMetadataResponse, WorkspaceCoChangeEntry } from "@repowise-dev/api-client/types";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { AiPromptModal } from "@repowise-dev/ui/health/ai-prompt-modal";
import type { AiPromptFlavor } from "@repowise-dev/ui/health/ai-prompt-builder";
import { CoChangeTable, coChangeKey } from "@repowise-dev/ui/workspace/co-change-table";
import { RepoPairTable, type RepoPairSummary } from "@repowise-dev/ui/workspace/repo-pair-table";
import {
  CoChangePairDrawer,
  type CoChangeFileHistory,
  type Loadable,
} from "@repowise-dev/ui/workspace/co-change-pair-drawer";
import {
  buildCoChangePairAiPrompt,
  buildCoChangeRepoPairAiPrompt,
} from "@repowise-dev/ui/workspace/co-change-ai-prompt";
import {
  coChangeRepoPairId,
  nearbyCommits,
  type CoChangeCaps,
} from "@repowise-dev/ui/workspace/co-change-facts";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { getWorkspaceCoChangeStructure } from "@/lib/api/workspace";
import { getGitMetadata } from "@/lib/api/git";

interface Props {
  coChanges: WorkspaceCoChangeEntry[];
  repoPairs: RepoPairSummary[];
  totalMined: number;
  /** Which of the miner's caps trimmed the list, as the server reports it. */
  caps: CoChangeCaps;
  /** Alias to indexed repo id; repos without one have no file pages. */
  repoIds: Record<string, string>;
  initialPair: string | null;
  initialQuery: string;
  initialOpen: string | null;
}

/** The minimal translator shape the copy helpers below need; next-intl's `t` fits. */
type Translator = (key: string, values?: Record<string, string | number>) => string;

type PromptTarget = { kind: "file" } | { kind: "repo"; pair: RepoPairSummary } | null;

/**
 * The rule that trimmed the stored pairs, as a clause.
 *
 * Named here rather than read from `@repowise-dev/ui`, which stays
 * language-agnostic: the clause is a sentence fragment, so it comes from the
 * catalog and the caller capitalises it.
 */
function capRule(t: Translator, caps: CoChangeCaps): string | null {
  if (caps.truncatedBy === "per_repo_pair" && caps.perRepoPairCap) {
    return t("scope.rulePerPair", { cap: caps.perRepoPairCap });
  }
  if (caps.truncatedBy === "total" && caps.totalCap) {
    return caps.perRepoPairCap
      ? t("scope.ruleTotalWithPerPair", {
          cap: caps.totalCap,
          perPair: caps.perRepoPairCap,
        })
      : t("scope.ruleTotal", { cap: caps.totalCap });
  }
  return caps.truncatedBy ? t("scope.ruleUnknown") : null;
}

const SEARCH_DEBOUNCE_MS = 150;

function flip(cc: WorkspaceCoChangeEntry): WorkspaceCoChangeEntry {
  return {
    ...cc,
    source_repo: cc.target_repo,
    source_file: cc.target_file,
    target_repo: cc.source_repo,
    target_file: cc.source_file,
  };
}

/** `repo/path`, so a query can name the repository too. */
function haystack(repo: string, file: string): string {
  return `${repo}/${file}`.toLowerCase();
}

/**
 * Write the reader's state to the URL without a navigation. The rows are all
 * on the client already, so a router push would only re-run the server page
 * to hand back the same data.
 */
function syncUrl(pair: string | null, q: string, cc: string | null) {
  const url = new URL(window.location.href);
  const set = (k: string, v: string | null) => (v ? url.searchParams.set(k, v) : url.searchParams.delete(k));
  set("pair", pair);
  set("q", q.trim() || null);
  set("cc", cc);
  window.history.replaceState(null, "", `${url.pathname}${url.search}`);
}

/** One side's per-repo git facts, or why there are none. Keyed like the docs
 *  viewer's fetch, so a file already seen there costs nothing here. */
function useFileHistory(repoId: string | undefined, path: string | undefined): Loadable<CoChangeFileHistory> {
  const t = useTranslations("coChanges");
  const { data, error } = useSWR<GitMetadataResponse | null>(
    repoId && path ? `git-meta:${repoId}:${path}` : null,
    () =>
      getGitMetadata(repoId!, path!).catch((err) => {
        if (err?.status === 404) return null;
        throw err;
      }),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  // Memoised so the history keeps its identity between renders, which is what
  // lets the reconstructed commits be computed once per pair.
  return useMemo((): Loadable<CoChangeFileHistory> => {
    if (!path) return { state: "loading" };
    if (!repoId) return { state: "unavailable", reason: t("reason.repoNotIndexed") };
    if (error) return { state: "unavailable", reason: t("reason.historyFailed") };
    if (data === undefined) return { state: "loading" };
    if (data === null) return { state: "unavailable", reason: t("reason.noGitHistory") };
    return {
      state: "ready",
      data: {
        commits90d: data.commit_count_90d,
        churnPercentile: data.churn_percentile,
        priorFixes: data.prior_defect_count ?? 0,
        isHotspot: data.is_hotspot,
        commits: (data.significant_commits ?? []).map((c) => ({
          sha: String(c.sha ?? ""),
          date: String(c.date ?? ""),
          message: typeof c.message === "string" ? c.message : null,
          author: typeof c.author === "string" ? c.author : null,
        })),
      },
    };
  }, [repoId, path, data, error, t]);
}

export function CoChangesExplorer({
  coChanges,
  repoPairs,
  totalMined,
  caps,
  repoIds,
  initialPair,
  initialQuery,
  initialOpen,
}: Props) {
  const t = useTranslations("coChanges");
  const [selectedPair, setSelectedPair] = useState<string | null>(initialPair);
  const [query, setQuery] = useState(initialQuery);
  const [debouncedQuery, setDebouncedQuery] = useState(initialQuery);
  const [openKey, setOpenKey] = useState<string | null>(initialOpen);
  const [prompt, setPrompt] = useState<PromptTarget>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    syncUrl(selectedPair, debouncedQuery, openKey);
  }, [selectedPair, debouncedQuery, openKey]);

  const byKey = useMemo(() => new Map(coChanges.map((c) => [coChangeKey(c), c])), [coChanges]);

  // Narrow by repository pair, then by path. A row whose match is only on its
  // second file is flipped so the searched file leads: the list then reads as
  // "what changes with this file".
  const rows = useMemo(() => {
    const terms = debouncedQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const out: WorkspaceCoChangeEntry[] = [];
    for (const c of coChanges) {
      if (selectedPair && coChangeRepoPairId(c) !== selectedPair) continue;
      if (terms.length === 0) {
        out.push(c);
        continue;
      }
      const a = haystack(c.source_repo, c.source_file);
      const b = haystack(c.target_repo, c.target_file);
      const onA = terms.every((t) => a.includes(t));
      const onB = terms.every((t) => b.includes(t));
      if (onA) out.push(c);
      else if (onB) out.push(flip(c));
    }
    return out;
  }, [coChanges, selectedPair, debouncedQuery]);

  const openPair = useMemo(() => {
    if (!openKey) return null;
    return rows.find((r) => coChangeKey(r) === openKey) ?? byKey.get(openKey) ?? null;
  }, [openKey, rows, byKey]);

  const onSelectRow = useCallback((cc: WorkspaceCoChangeEntry) => setOpenKey(coChangeKey(cc)), []);
  const onSelectPair = useCallback(
    (id: string) => setSelectedPair((cur) => (cur === id ? null : id)),
    [],
  );
  const onRepoPrompt = useCallback((pair: RepoPairSummary) => setPrompt({ kind: "repo", pair }), []);

  // Drawer evidence, fetched only while a pair is open and cached per pair.
  const { data: structureData, error: structureError } = useSWR(
    openPair ? `workspace:co-changes:structure:${coChangeKey(openPair)}` : null,
    () =>
      getWorkspaceCoChangeStructure({
        source_repo: openPair!.source_repo,
        source_file: openPair!.source_file,
        target_repo: openPair!.target_repo,
        target_file: openPair!.target_file,
      }),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  const structure = useMemo(
    () =>
      structureError
        ? ({ state: "unavailable", reason: t("reason.linksFailed") } as const)
        : structureData
          ? ({
              state: "ready",
              data: {
                pairLinks: structureData.pair_links,
                repoLinksTotal: structureData.repo_links_total,
                repoLinksByType: structureData.repo_links_by_type,
              },
            } as const)
          : ({ state: "loading" } as const),
    [structureData, structureError],
  );
  const sourceHistory = useFileHistory(
    openPair ? repoIds[openPair.source_repo] : undefined,
    openPair?.source_file,
  );
  const targetHistory = useFileHistory(
    openPair ? repoIds[openPair.target_repo] : undefined,
    openPair?.target_file,
  );
  // Once per pair, for both the drawer and the prompt.
  const commits = useMemo(
    () =>
      sourceHistory.state === "ready" && targetHistory.state === "ready"
        ? nearbyCommits(sourceHistory.data.commits, targetHistory.data.commits)
        : null,
    [sourceHistory, targetHistory],
  );

  const getPrompt = useMemo(() => {
    if (prompt?.kind === "repo") {
      const pairs = coChanges.filter((c) => coChangeRepoPairId(c) === prompt.pair.id);
      return (flavor: AiPromptFlavor) =>
        buildCoChangeRepoPairAiPrompt({
          repo1: prompt.pair.repo1,
          repo2: prompt.pair.repo2,
          pairs,
          flavor,
          caps,
        });
    }
    if (prompt?.kind === "file" && openPair) {
      const facts = (h: Loadable<CoChangeFileHistory>) =>
        h.state === "ready"
          ? {
              commits90d: h.data.commits90d,
              churnPercentile: h.data.churnPercentile,
              priorFixes: h.data.priorFixes,
              isHotspot: h.data.isHotspot,
            }
          : null;
      return (flavor: AiPromptFlavor) =>
        buildCoChangePairAiPrompt({
          pair: openPair,
          flavor,
          structure: structure.state === "ready" ? structure.data : null,
          sourceFacts: facts(sourceHistory),
          targetFacts: facts(targetHistory),
          commits: commits ?? [],
        });
    }
    return null;
  }, [prompt, coChanges, caps, openPair, structure, sourceHistory, targetHistory, commits]);

  const selected = selectedPair ? repoPairs.find((p) => p.id === selectedPair) ?? null : null;
  const trimmed = debouncedQuery.trim();
  const inScope = selected ? selected.filePairCount : coChanges.length;
  const rule = capRule(t, caps);
  const dropped = totalMined - coChanges.length;

  const scopeValues = {
    rows: formatNumber(rows.length),
    scope: formatNumber(inScope),
    total: formatNumber(totalMined),
    query: trimmed,
  };
  const scope = [
    trimmed
      ? selected
        ? t("scope.filteredInPair", {
            ...scopeValues,
            repo1: selected.repo1,
            repo2: selected.repo2,
          })
        : t("scope.filtered", scopeValues)
      : selected
        ? t("scope.selected", {
            rows: formatNumber(rows.length),
            repo1: selected.repo1,
            repo2: selected.repo2,
          })
        : t("scope.all", {
            rows: formatNumber(rows.length),
            total: formatNumber(totalMined),
          }),
    rule && dropped > 0
      ? t("scope.capped", {
          rule: `${rule[0]!.toUpperCase()}${rule.slice(1)}`,
          dropped: formatNumber(dropped),
        })
      : "",
  ].join("");

  return (
    <>
      <OverviewSection
        title={t("pairs.title")}
        description={
          caps.truncatedBy === "per_repo_pair" && caps.perRepoPairCap
            ? t("pairs.descriptionCapped", { cap: caps.perRepoPairCap })
            : t("pairs.descriptionRanked")
        }
      >
        <RepoPairTable
          repoPairs={repoPairs}
          selectedPairId={selectedPair}
          onSelectPair={onSelectPair}
          onPrompt={onRepoPrompt}
        />
      </OverviewSection>

      <OverviewSection
        title={
          selected
            ? t("files.titleScoped", {
                repo1: selected.repo1,
                repo2: selected.repo2,
              })
            : t("files.titleAll")
        }
        description={scope}
      >
        <div className="flex flex-wrap items-center gap-3">
          <label className="relative flex min-w-0 max-w-[420px] flex-1 items-center">
            <span className="sr-only">{t("files.filterLabel")}</span>
            <Search
              className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-[var(--color-text-tertiary)]"
              aria-hidden
            />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("files.searchPlaceholder")}
              className="h-8 w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] pl-8 pr-3 font-mono text-xs text-[var(--color-text-primary)] placeholder:font-sans placeholder:text-[var(--color-text-tertiary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
            />
          </label>
          {selected && (
            <button
              type="button"
              onClick={() => setSelectedPair(null)}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
            >
              <X className="h-3 w-3" aria-hidden />
              {t("files.clearPair")}
            </button>
          )}
        </div>
        {rows.length === 0 ? (
          <EmptyState
            title={t("files.emptyTitle")}
            description={t("files.emptyDescription", {
              query: trimmed,
              count: formatNumber(coChanges.length),
            })}
          />
        ) : (
          <CoChangeTable coChanges={rows} onSelect={onSelectRow} selectedKey={openKey} />
        )}
      </OverviewSection>

      <CoChangePairDrawer
        pair={openPair}
        onClose={() => setOpenKey(null)}
        structure={structure}
        sourceHistory={sourceHistory}
        targetHistory={targetHistory}
        commits={commits}
        fileHref={(repo, path) => {
          const id = repoIds[repo];
          return id ? fileEntityPath(`/repos/${id}`, path) : null;
        }}
        contractHref={(l) =>
          `/workspace/contracts?${new URLSearchParams({
            contract: l.contract_id,
            repo: l.provider_repo,
            file: l.provider_file,
          }).toString()}`
        }
        LinkComponent={Link}
        onGeneratePrompt={() => setPrompt({ kind: "file" })}
      />

      <AiPromptModal
        open={prompt !== null}
        onOpenChange={(o) => !o && setPrompt(null)}
        getPrompt={getPrompt}
        filePath={
          prompt?.kind === "repo"
            ? `${prompt.pair.repo1} and ${prompt.pair.repo2}`
            : openPair
              ? `${openPair.source_file} and ${openPair.target_file}`
              : null
        }
        title={t("prompt.title")}
        description={
          prompt?.kind === "repo"
            ? t("prompt.descriptionRepo")
            : t("prompt.descriptionFile")
        }
      />
    </>
  );
}
