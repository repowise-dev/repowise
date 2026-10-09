import type { Metadata } from "next";
import Link from "next/link";
import { Link2 } from "lucide-react";
import { getTranslations } from "next-intl/server";
import type { ExtractionDiagnostics } from "@repowise-dev/api-client/types";
import { PageShell } from "@repowise-dev/ui/shared";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { groupOrphanProviders } from "@repowise-dev/ui/workspace/contract-facts";
import { MAX_PROMPT_ROWS } from "@repowise-dev/ui/workspace/contract-ai-prompt";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import {
  getWorkspace,
  getWorkspaceContracts,
  getWorkspaceDiagnostics,
} from "@/lib/api/workspace";
import { BreakingChangesSection } from "./breaking-changes-section";
import { ContractDrawerProvider } from "./contract-drawer-host";
import { ContractListControls, ContractListPager } from "./contract-filters";
import { contractsListHref, type ContractListFilters } from "./contract-href";
import { ContractsTable } from "./contracts-table";
import { LinksSection } from "./links-table";
import { NeedsAttention } from "./needs-attention";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("contracts");
  return { title: t("title") };
}

export const revalidate = 30;

/** The minimal translator shape the copy helpers below need; next-intl's `t` fits. */
type Translator = (key: string, values?: Record<string, string | number>) => string;

/** Rows per page of the full list. The server caps a page at 1,000. */
const PAGE_SIZE = 100;

type Props = {
  searchParams: Promise<{
    type?: string;
    repo?: string;
    role?: string;
    linked?: string;
    q?: string;
    page?: string;
    contract?: string;
    file?: string;
  }>;
};

/**
 * Contracts detected across the workspace, which of them matched, and which
 * need a look.
 *
 * The figures come from `/api/workspace/diagnostics`, which knows the
 * workspace-wide denominators, and deliberately do not move when the list is
 * filtered. The list pages on the server, so every contract is reachable and
 * the count beside it is the count of what the filters return.
 *
 * `?contract=&repo=&file=` opens the drawer on load (the System Map links here
 * that way). While it is present `repo` names the drawer's contract, not a list
 * filter, so a deep link never lands on a list narrowed by accident.
 */
export default async function ContractsPage({ searchParams }: Props) {
  const t = await getTranslations("contracts");
  const sp = await searchParams;
  const deepLink =
    sp.contract && sp.repo && sp.file
      ? { contract_id: sp.contract, repo: sp.repo, file_path: sp.file }
      : null;
  const filters: ContractListFilters = {
    type: sp.type || undefined,
    repo: deepLink ? undefined : sp.repo || undefined,
    role: sp.role || undefined,
    linked: sp.linked === "yes" || sp.linked === "no" ? sp.linked : undefined,
    // A contract link without a file (an edge ref) cannot name one declaration,
    // so it lands on the list searched for that id instead.
    q: sp.q?.trim() || (!deepLink && sp.contract) || undefined,
  };
  const page = Math.max(1, Number.parseInt(sp.page ?? "1", 10) || 1);

  const [ct, diag, ws] = await Promise.allSettled([
    getWorkspaceContracts({
      contract_type: filters.type,
      repo: filters.repo,
      role: filters.role,
      q: filters.q,
      ...(filters.linked ? { linked: filters.linked === "yes" } : {}),
      // The links section fetches its own; resending them on every page turn
      // was most of this request.
      include_links: false,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    }),
    // Workspace-wide and unchanged by the filters, so a page turn or a search
    // reuses them instead of refetching.
    getWorkspaceDiagnostics({ next: { revalidate: 30 } }),
    getWorkspace({ next: { revalidate: 30 } }),
  ]);

  const data = ct.status === "fulfilled" ? ct.value : null;
  const diagnostics = diag.status === "fulfilled" ? diag.value : null;
  const workspace = ws.status === "fulfilled" ? ws.value : null;
  const repos = workspace?.repos.map((r) => r.alias) ?? [];
  // The workspace-wide breakdown, not `data.by_type`: that one is counted after
  // the filters run, so picking a type would leave only the type already picked.
  const byType = workspace?.contract_summary?.by_type ?? null;
  const workspaceTotal = byType ? Object.values(byType).reduce((a, b) => a + b, 0) : null;
  const repoIds: Record<string, string> = {};
  for (const r of workspace?.repos ?? []) {
    if (r.repo_id) repoIds[r.alias] = r.repo_id;
  }

  const rows = data?.contracts ?? [];
  const total = data?.total_contracts ?? 0;
  const filtered = Boolean(filters.type || filters.repo || filters.role || filters.linked || filters.q);

  // The lede carries matched links, so the ribbon does not repeat it.
  const ribbon: RibbonStat[] = [
    {
      label: t("ribbon.providers"),
      value: diagnostics ? formatNumber(diagnostics.total_providers) : "",
      sub: t("ribbon.providersSub"),
    },
    {
      label: t("ribbon.consumers"),
      value: diagnostics ? formatNumber(diagnostics.total_consumers) : "",
      sub: t("ribbon.consumersSub"),
    },
    {
      label: t("ribbon.unmatched"),
      value: diagnostics ? formatNumber(diagnostics.unmatched_consumers.length) : "",
      sub: unmatchedReasonSub(t, diagnostics),
    },
    {
      label: t("ribbon.orphans"),
      value: diagnostics ? formatNumber(diagnostics.orphan_providers.length) : "",
      sub: t("ribbon.orphansSub"),
    },
    {
      // Extraction reporting on its own recall: the denominator is calls a
      // dialect located, so it says nothing about calls nothing recognised.
      label: t("ribbon.httpResolved"),
      value:
        diagnostics?.http_consumer_coverage != null
          ? `${Math.floor(diagnostics.http_consumer_coverage * 100)}%`
          : "",
      sub: diagnostics?.http_consumers_unresolved
        ? t("ribbon.httpUnresolved", {
            count: formatNumber(diagnostics.http_consumers_unresolved),
          })
        : t("ribbon.httpSub"),
    },
  ];

  return (
    <PageShell
      title={t("title")}
      icon={<Link2 className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
      description={t("description")}
    >
      <PageLede
        label={t("links.title")}
        value={diagnostics ? formatNumber(diagnostics.total_links) : t("ledeUnknown")}
        unit={t("ledeUnit")}
        layout="beside"
      >
        {diagnostics ? (
          <>
            <p>{t("ledeLinkLine1")}</p>
            <p>{t("ledeLinkLine2", { attention: t("attention.title") })}</p>
          </>
        ) : (
          <p>{t("ledeNoDiagnostics")}</p>
        )}
      </PageLede>

      <StatRibbon stats={ribbon} />

      <ContractDrawerProvider repoIds={repoIds} initial={deepLink}>
        <BreakingChangesSection repoIds={repoIds} />

        {diagnostics ? (
          <OverviewSection title={t("attention.title")}>
            <NeedsAttention
              unmatched={diagnostics.unmatched_consumers}
              // Counts plus a prompt's worth of rows per group, not all 1,000+.
              orphanGroups={groupOrphanProviders(diagnostics.orphan_providers, MAX_PROMPT_ROWS)}
            />
          </OverviewSection>
        ) : null}

        <LinksSection type={filters.type} repo={filters.repo} q={filters.q} />

        <OverviewSection
          id="all-contracts"
          title={t("list.title")}
          description={data ? listDescription(t, total, workspaceTotal, filtered) : undefined}
        >
          <ContractListControls filters={filters} repos={repos} byType={byType} />
          {rows.length === 0 ? (
            // One quiet sentence, like every other state with nothing in it.
            <p className="text-xs leading-relaxed text-[var(--color-text-secondary)]">
              {!data
                ? t("list.loadFailed")
                : total > 0
                  ? t.rich("list.pagePastEnd", {
                      page: formatNumber(page),
                      total,
                      first: (chunks) => (
                        <Link
                          href={contractsListHref({ ...filters, page: 1 })}
                          className="text-[var(--color-accent-primary)] hover:underline"
                        >
                          {chunks}
                        </Link>
                      ),
                    })
                  : filtered
                    ? t("list.noMatch")
                    : t("list.noneDetected")}
            </p>
          ) : (
            <>
              <ContractsTable contracts={rows} showType={!filters.type} />
              <ContractListPager
                filters={filters}
                page={page}
                pageSize={PAGE_SIZE}
                shown={rows.length}
                total={total}
              />
            </>
          )}
        </OverviewSection>
      </ContractDrawerProvider>

      <p className="text-xs text-[var(--color-text-tertiary)]">
        {t.rich("footer.note", {
          link: (chunks) => (
            <Link
              href="/workspace/system-map"
              className="text-[var(--color-accent-primary)] hover:underline"
            >
              {chunks}
            </Link>
          ),
        })}
      </p>
    </PageShell>
  );
}

/** Say exactly what the list holds against the workspace it came from. */
function listDescription(
  t: Translator,
  total: number,
  workspaceTotal: number | null,
  filtered: boolean,
): string {
  if (!filtered) {
    return t("list.description", { total, pageSize: PAGE_SIZE });
  }
  return workspaceTotal != null
    ? t("list.filteredOf", { total, workspaceTotal })
    : t("list.filtered", { total });
}

/**
 * The unmatched-reason short labels. The reason codes come from the system
 * graph; the prose in `@repowise-dev/ui` stays English, so the label the ribbon
 * shows is named here instead.
 */
const REASON_KEYS: Record<string, string> = {
  no_provider: "reasons.no_provider",
  unlinked: "reasons.unlinked",
  internal_only: "reasons.internal_only",
  external_host: "reasons.external_host",
};

/** Name why consumers went unmatched, since the count alone invites the wrong read. */
function unmatchedReasonSub(
  t: Translator,
  diagnostics: ExtractionDiagnostics | null,
): string {
  if (!diagnostics) return "";
  const reasons = Object.entries(diagnostics.unmatched_by_reason ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  if (reasons.length === 0) return t("reasons.everyMatched");
  return reasons
    .map(([reason, n]) => `${n} ${t(REASON_KEYS[reason] ?? "reasons.unknown")}`)
    .join(", ");
}
