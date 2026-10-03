import type { Metadata } from "next";
import Link from "next/link";
import { Link2 } from "lucide-react";
import type { ExtractionDiagnostics } from "@repowise-dev/api-client/types";
import { PageShell } from "@repowise-dev/ui/shared";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import {
  groupOrphanProviders,
  unmatchedReasonCopy,
} from "@repowise-dev/ui/workspace/contract-facts";
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

export const metadata: Metadata = { title: "Contracts" };

export const revalidate = 30;

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
      label: "Providers",
      value: diagnostics ? formatNumber(diagnostics.total_providers) : "",
      sub: "routes, tables and exports published",
    },
    {
      label: "Consumers",
      value: diagnostics ? formatNumber(diagnostics.total_consumers) : "",
      sub: "call sites resolved to a contract",
    },
    {
      label: "Unmatched consumers",
      value: diagnostics ? formatNumber(diagnostics.unmatched_consumers.length) : "",
      sub: unmatchedReasonSub(diagnostics),
    },
    {
      label: "Providers with no caller",
      value: diagnostics ? formatNumber(diagnostics.orphan_providers.length) : "",
      sub: "in this workspace; not the same as unused",
    },
    {
      // Extraction reporting on its own recall: the denominator is calls a
      // dialect located, so it says nothing about calls nothing recognised.
      label: "HTTP calls resolved",
      value:
        diagnostics?.http_consumer_coverage != null
          ? `${Math.floor(diagnostics.http_consumer_coverage * 100)}%`
          : "",
      sub: diagnostics?.http_consumers_unresolved
        ? `${formatNumber(diagnostics.http_consumers_unresolved)} located but not resolvable`
        : "of the client calls extraction located",
    },
  ];

  return (
    <PageShell
      title="Contracts"
      icon={<Link2 className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
      description="Routes, tables and exports one repository publishes and another consumes."
    >
      <PageLede
        label="Matched links"
        value={diagnostics ? formatNumber(diagnostics.total_links) : "Unknown"}
        unit="provider to consumer"
        layout="beside"
      >
        {diagnostics ? (
          <>
            <p>
              Each link is a call site resolved to the route, table or export that serves it, in
              another repository or service.
            </p>
            <p>
              It is a match on the path or name, not proof both sides share a schema. Calls that
              matched nothing and providers nothing calls are under Needs attention.
            </p>
          </>
        ) : (
          <p>
            Extraction diagnostics are not available, so the totals cannot be shown. Run a
            workspace sync to rebuild them.
          </p>
        )}
      </PageLede>

      <StatRibbon stats={ribbon} />

      <ContractDrawerProvider repoIds={repoIds} initial={deepLink}>
        <BreakingChangesSection repoIds={repoIds} />

        {diagnostics ? (
          <OverviewSection title="Needs attention">
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
          title="All detected contracts"
          description={data ? listDescription(total, workspaceTotal, filtered) : undefined}
        >
          <ContractListControls filters={filters} repos={repos} byType={byType} />
          {rows.length === 0 ? (
            // One quiet sentence, like every other state with nothing in it.
            <p className="text-xs leading-relaxed text-[var(--color-text-secondary)]">
              {!data ? (
                "The contract list could not be loaded. The API may be restarting; reload in a moment."
              ) : total > 0 ? (
                <>
                  Page {formatNumber(page)} is past the end of this list, which has{" "}
                  {formatNumber(total)} {total === 1 ? "contract" : "contracts"}.{" "}
                  <Link
                    href={contractsListHref({ ...filters, page: 1 })}
                    className="text-[var(--color-accent-primary)] hover:underline"
                  >
                    Go to the first page
                  </Link>
                  .
                </>
              ) : filtered ? (
                "No contracts match these filters. Clear the search or a filter to widen the list."
              ) : (
                "No contracts detected. They are found during a workspace sync, by reading the routes each repository serves and the calls the others make."
              )}
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
        Contracts are matched on path. Two routes that share a path are one contract here, so a
        repository can appear against a contract it declares for its own use.{" "}
        <Link
          href="/workspace/system-map"
          className="text-[var(--color-accent-primary)] hover:underline"
        >
          See how they connect
        </Link>
        .
      </p>
    </PageShell>
  );
}

/** Say exactly what the list holds against the workspace it came from. */
function listDescription(total: number, workspaceTotal: number | null, filtered: boolean): string {
  const noun = total === 1 ? "contract" : "contracts";
  if (!filtered) {
    return `All ${formatNumber(total)} ${noun} detected across the workspace, ${PAGE_SIZE} to a page. One contract can be declared in several places, so a name may repeat.`;
  }
  const of = workspaceTotal != null ? ` of ${formatNumber(workspaceTotal)}` : "";
  return `${formatNumber(total)}${of} ${noun} match these filters.`;
}

/** Name why consumers went unmatched, since the count alone invites the wrong read. */
function unmatchedReasonSub(diagnostics: ExtractionDiagnostics | null): string {
  if (!diagnostics) return "";
  const reasons = Object.entries(diagnostics.unmatched_by_reason ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  if (reasons.length === 0) return "every consumer matched a provider";
  return reasons.map(([reason, n]) => `${n} ${unmatchedReasonCopy(reason).short}`).join(", ");
}
