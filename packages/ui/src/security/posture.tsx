import * as React from "react";
import { PageLede } from "../shared/page-lede";
import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import type { SecurityFinding, SecuritySummary } from "@repowise-dev/types";
import { formatNumber } from "../lib/format";

export type { SecurityPathClass } from "@repowise-dev/types";

/** The scanner's three severities; anything unrecognised counts as low. */
export type SecurityLevel = "high" | "med" | "low";

export function securityLevel(severity: string): SecurityLevel {
  return severity === "high" || severity === "med" ? severity : "low";
}

export interface SecurityPostureCounts {
  total: number;
  /** Matches in shipped source. */
  source: number;
  /** Matches in tests and docs. */
  elsewhere: number;
  sourceHigh: number;
}

/** The lede's counts from the server's summary; `null` on a server without them. */
export function postureCountsFromSummary(
  summary: SecuritySummary | null | undefined,
): SecurityPostureCounts | null {
  const c = summary?.class_counts;
  if (!c || summary?.total == null) return null;
  const source = c.source ?? 0;
  return {
    total: summary.total,
    source,
    elsewhere: (c.test ?? 0) + (c.docs ?? 0),
    sourceHigh: summary.source_high ?? 0,
  };
}

/** The same counts over listed rows, by the class the server gave each one. */
export function countSecurityFindings(findings: SecurityFinding[]): SecurityPostureCounts {
  const c: SecurityPostureCounts = { total: findings.length, source: 0, elsewhere: 0, sourceHigh: 0 };
  for (const f of findings) {
    if ((f.path_class ?? "source") === "source") {
      c.source += 1;
      if (securityLevel(f.severity) === "high") c.sourceHigh += 1;
    } else {
      c.elsewhere += 1;
    }
  }
  return c;
}

export interface SecurityPostureLabels {
  figure: string;
  figureHint: string;
  /** Unit beside the figure. `capped` means the total is a floor. */
  unit: (total: number, capped: boolean) => string;
  sentence: (counts: SecurityPostureCounts) => string;
  /** Receives a relative time, e.g. "3 days ago". */
  scanned: (when: string) => string;
  scanTimeUnknown: string;
}

const n = (v: number) => formatNumber(v);
const plural = (v: number, one: string, many: string) => `${n(v)} ${v === 1 ? one : many}`;

export const DEFAULT_SECURITY_POSTURE_LABELS: SecurityPostureLabels = {
  figure: "In shipped source",
  figureHint:
    "Pattern matches outside test, spec, fixture, mock, example and docs paths. These are the ones to confirm first.",
  unit: (total, capped) => `of ${n(total)}${capped ? "+" : ""} ${total === 1 ? "finding" : "findings"}`,
  sentence: (c) => {
    if (c.source === 0) {
      return `Nothing matched in shipped source. ${
        c.elsewhere === 1 ? "The one match is" : `All ${n(c.elsewhere)} matches are`
      } in tests and docs, listed so you can confirm they are fixtures.`;
    }
    const lead = `The pattern scan for secrets and dangerous calls matched ${plural(
      c.source,
      "place",
      "places",
    )} in shipped source, ${n(c.sourceHigh)} of them high.`;
    if (c.elsewhere === 0) return lead;
    return `${lead} ${n(c.elsewhere)} more ${c.elsewhere === 1 ? "is" : "are"} in tests and docs, listed after shipped code.`;
  },
  scanned: (when) => `Scanned ${when}.`,
  scanTimeUnknown: "Scan time not recorded.",
};

export interface SecurityPostureProps {
  /** The server's counts (`postureCountsFromSummary`); preferred over `findings`. */
  counts?: SecurityPostureCounts | null;
  /** Listed rows, counted when the server sent no counts. */
  findings?: SecurityFinding[];
  /** Relative scan time ("3 days ago"), or null when the server has none. */
  scannedAgo: string | null;
  /** True when the list was cut at the request limit, so counts are a floor. */
  capped?: boolean;
  labels?: Partial<SecurityPostureLabels>;
}

/**
 * The security page's lede: the figure to act on (matches in shipped source),
 * the sentence that scopes it, and when the scan ran.
 * Never coloured: a pattern match is a lead to confirm, not a live path to
 * harm.
 */
export function SecurityPosture({
  counts,
  findings,
  scannedAgo,
  capped = false,
  labels,
}: SecurityPostureProps) {
  const l = { ...DEFAULT_SECURITY_POSTURE_LABELS, ...labels };
  const listed = React.useMemo(() => countSecurityFindings(findings ?? []), [findings]);
  const c = counts ?? listed;

  return (
    <PageLede
      label={l.figure}
      labelHint={l.figureHint}
      value={n(c.source)}
      unit={l.unit(c.total, !counts && capped)}
      layout="beside"
    >
      {/* One paragraph so the scan line stays under the sentence when the
          lede flows into columns. */}
      <p>
        {l.sentence(c)}
        <span className="mt-2 block text-xs text-[var(--color-text-tertiary)]">
          {scannedAgo ? l.scanned(scannedAgo) : l.scanTimeUnknown}
        </span>
      </p>
    </PageLede>
  );
}

/** Loading silhouette in the shape of the posture plus the findings list. */
export function SecurityPostureSkeleton({ label = "Loading security findings" }: { label?: string }) {
  return (
    <SkeletonRegion label={label} className="space-y-6">
      <div className="flex flex-col gap-5 lg:flex-row lg:gap-12">
        <div className="space-y-3 lg:w-[220px]">
          <Skeleton className="h-3 w-28" />
          <Skeleton className="h-11 w-20" />
        </div>
        <div className="w-full max-w-[62ch] space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="h-3 w-32" />
        </div>
      </div>
      <div className="space-y-3 border-t border-[var(--color-border-default)] pt-6 sm:pt-8">
        <Skeleton className="h-5 w-32" />
        <div className="flex flex-wrap gap-2">
          <Skeleton className="h-8 w-full sm:w-72" />
          <Skeleton className="h-8 w-56" />
        </div>
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-9 w-full" />
        ))}
      </div>
    </SkeletonRegion>
  );
}
