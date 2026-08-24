import type { Metadata } from "next";
import Link from "next/link";
import { Link2 } from "lucide-react";
import type { ExtractionDiagnostics } from "@repowise-dev/api-client/types";
import { PageShell, EmptyState } from "@repowise-dev/ui/shared";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { ContractLinksTable } from "@repowise-dev/ui/workspace/contract-links-table";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import {
  getWorkspace,
  getWorkspaceContracts,
  getWorkspaceDiagnostics,
} from "@/lib/api/workspace";
import { ContractFilters } from "./contract-filters";
import { ContractsTable } from "./contracts-table";

export const metadata: Metadata = { title: "Contracts" };

export const revalidate = 30;

/** Rows the table draws. The server caps at 1,000; this is the page's own
 *  window and it is always reported against the filtered total below. */
const ROW_WINDOW = 200;

type Props = {
  searchParams: Promise<{ type?: string; repo?: string; role?: string }>;
};

/**
 * Contracts detected across the workspace, and which of them matched.
 *
 * The four `MetricCard`s this replaces could not be restyled into a ribbon,
 * because three of them were counting the wrong things. "Total Contracts" and
 * "Unmatched" were both derived from the contract rows, which arrive filtered
 * *and* paginated — so "Unmatched" was computed over one page of 200 and
 * reported as a workspace figure, and it moved whenever a filter changed. "By
 * Type" was arithmetically identical to "Total Contracts", since the sum of a
 * breakdown is the thing it breaks down.
 *
 * The figures come from `/api/workspace/diagnostics` now, which is the
 * endpoint that already knows the denominators: how many providers and
 * consumers extraction found, how many linked, and why the rest did not. Those
 * are workspace-wide and deliberately do not move when the table is filtered.
 */
export default async function ContractsPage({ searchParams }: Props) {
  const { type, repo, role } = await searchParams;

  const [ct, diag, ws] = await Promise.allSettled([
    getWorkspaceContracts({
      contract_type: type || undefined,
      repo: repo || undefined,
      role: role || undefined,
      limit: ROW_WINDOW,
    }),
    getWorkspaceDiagnostics(),
    getWorkspace(),
  ]);

  const data = ct.status === "fulfilled" ? ct.value : null;
  const diagnostics = diag.status === "fulfilled" ? diag.value : null;
  const workspace = ws.status === "fulfilled" ? ws.value : null;
  const repos = workspace?.repos.map((r) => r.alias) ?? [];
  // The workspace-wide breakdown, not `data.by_type`: that one is counted
  // after the filters run, so picking a type would leave the select holding
  // only the type already picked.
  const byType = workspace?.contract_summary?.by_type ?? null;

  const rows = data?.contracts ?? [];
  const links = data?.links ?? [];
  const filtered = Boolean(type || repo || role);

  const ribbon: RibbonStat[] = [
    {
      label: "Providers",
      value: diagnostics ? formatNumber(diagnostics.total_providers) : "—",
      sub: "routes, topics and tables published",
    },
    {
      label: "Consumers",
      value: diagnostics ? formatNumber(diagnostics.total_consumers) : "—",
      sub: "call sites resolved to a contract",
    },
    {
      label: "Matched links",
      value: diagnostics ? formatNumber(diagnostics.total_links) : "—",
      sub: "provider joined to consumer",
    },
    {
      label: "Unmatched consumers",
      value: diagnostics ? formatNumber(diagnostics.unmatched_consumers.length) : "—",
      sub: unmatchedReasonSub(diagnostics),
    },
    {
      label: "Unused providers",
      value: diagnostics ? formatNumber(diagnostics.orphan_providers.length) : "—",
      sub: "nothing in the workspace calls them",
    },
    {
      // Extraction reporting on its own recall. The denominator is calls a
      // dialect located, so this is honest about what it covers and silent
      // about calls nothing recognised — it is not total recall.
      label: "HTTP calls resolved",
      value:
        diagnostics?.http_consumer_coverage != null
          ? `${Math.floor(diagnostics.http_consumer_coverage * 100)}%`
          : "—",
      sub: diagnostics?.http_consumers_unresolved
        ? `${formatNumber(diagnostics.http_consumers_unresolved)} located but not resolvable`
        : "of the client calls extraction located",
    },
  ];

  return (
    <PageShell
      title="Contracts"
      icon={<Link2 className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
      description="Routes, topics and tables one repository publishes and another consumes."
    >
      <PageLede
        label="Matched links"
        value={diagnostics ? formatNumber(diagnostics.total_links) : "—"}
        unit="provider to consumer"
        layout="beside"
      >
        {diagnostics ? (
          <>
            <p>
              Extraction found {formatNumber(diagnostics.total_providers)} providers and{" "}
              {formatNumber(diagnostics.total_consumers)} consumers across the workspace, and
              joined {formatNumber(diagnostics.total_links)} of them into cross-repo links. A
              link means a call site was resolved to the code that serves it, not that the two
              were declared against a shared schema.
            </p>
            <p>
              {formatNumber(diagnostics.orphan_providers.length)} providers have no caller in
              this workspace. Read that against extraction&rsquo;s own coverage rather than on
              its own: an endpoint looks unused both when nothing calls it and when the call
              was written in a form this analysis could not follow.
              {diagnostics.http_consumers_unresolved > 0 ? (
                <>
                  {" "}
                  {formatNumber(diagnostics.http_consumers_unresolved)} HTTP client calls were
                  located here but could not be resolved to an endpoint, so some of those
                  providers are called by code this page cannot yet name.
                </>
              ) : null}
            </p>
          </>
        ) : (
          <p>
            Extraction diagnostics are not available, so the totals below cannot be shown.
            Run a workspace sync to rebuild them.
          </p>
        )}
      </PageLede>

      <StatRibbon stats={ribbon} />

      {links.length > 0 && (
        <OverviewSection
          title="Matched links"
          description={
            filtered
              ? "Links matching the current filter, provider on the left and consumer on the right."
              : "Every provider joined to the consumer that calls it, with the confidence of the match."
          }
        >
          <ContractLinksTable links={links} />
        </OverviewSection>
      )}

      <OverviewSection
        title="All detected contracts"
        description={tableDescription(rows.length, data?.total_contracts ?? 0, filtered)}
        action={<ContractFilters repos={repos} byType={byType} />}
      >
        {rows.length === 0 ? (
          <EmptyState
            className="p-6"
            title={filtered ? "No contracts match this filter" : "No contracts detected"}
            description={
              filtered
                ? "Clear a filter to widen the search."
                : "Contracts are detected during a workspace sync, by reading the routes each repository serves and the calls the others make."
            }
          />
        ) : (
          <ContractsTable contracts={rows} />
        )}
      </OverviewSection>

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

/**
 * Say the bound. The endpoint pages the rows but reports the unpaged total, so
 * without this the heading claims a number the table does not draw.
 */
function tableDescription(shown: number, total: number, filtered: boolean): string {
  const scope = filtered ? "matching the current filter" : "detected across the workspace";
  if (total === 0) return `Every contract ${scope}.`;
  if (shown >= total) {
    return `All ${formatNumber(total)} ${total === 1 ? "contract" : "contracts"} ${scope}. One contract can be declared in several places, so a name may repeat.`;
  }
  return `Showing ${formatNumber(shown)} of ${formatNumber(total)} contracts ${scope}. One contract can be declared in several places, so a name may repeat.`;
}

/** Name why consumers went unmatched, since the count alone invites the wrong read. */
function unmatchedReasonSub(diagnostics: ExtractionDiagnostics | null): string {
  if (!diagnostics) return "";
  const reasons = Object.entries(diagnostics.unmatched_by_reason ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  if (reasons.length === 0) return "every consumer matched a provider";
  return reasons.map(([reason, n]) => `${n} ${REASON_LABEL[reason] ?? reason}`).join(", ");
}

const REASON_LABEL: Record<string, string> = {
  no_provider: "no provider found",
  internal_only: "internal to one repo",
  external_host: "external host",
  unlinked: "unlinked",
};
