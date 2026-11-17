import type { ExtractionDiagnostics } from "@repowise-dev/types/workspace";
import type { RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { inServiceConsumers } from "@repowise-dev/ui/workspace/contract-facts";

/** The `linkCoverage` message namespace; next-intl's `t` fits. */
type Translator = (key: string, values?: Record<string, string | number>) => string;

/**
 * Where the workspace's HTTP client calls ended up, split the way the CLI
 * headline reads: linked to another service, served inside their own service,
 * and read but not linked to anything.
 */
export interface HttpLinkCoverage {
  /** Calls read, plus located-but-unresolved, plus one per file no dialect reads. */
  seen: number;
  /** HTTP consumer contracts read, linked or not. */
  read: number;
  linked: number;
  /** Calls a repo makes to its own service: served, so never a missing provider. */
  inService: number;
  /** Read calls that reached no other service and are not in-service. */
  readNotLinked: number;
}

/**
 * The linked / in-service / read split, or null when the artifact predates the
 * linked count or the network probe, or saw no calls (0 of 0 is not 100%).
 * Mirrors `ExtractionDiagnostics.http_calls_seen` in core.
 */
export function httpLinkCoverage(diag: ExtractionDiagnostics): HttpLinkCoverage | null {
  const linked = diag.http_consumers_linked;
  const missed = diag.unrecognised_call_file_count;
  if (linked == null || missed == null) return null;
  const read = diag.repo_breakdown.reduce((n, r) => n + (r.consumers_by_type.http ?? 0), 0);
  const seen = read + (diag.http_consumers_unresolved ?? 0) + missed;
  if (seen <= 0) return null;
  const inService = inServiceConsumers(diag).filter((u) => u.contract_type === "http").length;
  return {
    seen,
    read,
    linked,
    inService,
    readNotLinked: Math.max(0, read - linked - inService),
  };
}

/** Whole percent, floored like the CLI so 99.6% never reads as 100%. */
export function percentOf(part: number, whole: number): string {
  return `${Math.floor((part * 100) / whole)}%`;
}

/**
 * The ribbon stat for the CLI headline "N of M linked to another service; K
 * read". Null on an artifact without the counts, so the page keeps its old stat.
 */
export function httpLinkedStat(t: Translator, diag: ExtractionDiagnostics): RibbonStat | null {
  const c = httpLinkCoverage(diag);
  if (!c) return null;
  return {
    label: t("label"),
    value: percentOf(c.linked, c.seen),
    sub: t("sub", {
      linked: formatNumber(c.linked),
      seen: formatNumber(c.seen),
      read: formatNumber(c.read),
    }),
    hint: t("hint", {
      readNotLinked: formatNumber(c.readNotLinked),
      inService: formatNumber(c.inService),
    }),
  };
}

/** What the unmatched count leaves out: the in-service calls, named only when there are some. */
export function unmatchedHint(t: Translator, diag: ExtractionDiagnostics): string {
  const inService = inServiceConsumers(diag).length;
  const base = t("unmatchedHint");
  return inService > 0 ? `${base} ${t("inServiceHint", { count: inService })}` : base;
}
