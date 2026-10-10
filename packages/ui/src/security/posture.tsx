import * as React from "react";
import { PageLede } from "../shared/page-lede";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import type { SecurityFinding } from "@repowise-dev/types";

export type SecurityPathClass = "source" | "test" | "docs";

// Client-side guess at where a match lives, mirroring the scanner's own
// low-severity path tokens. Ceiling: a heuristic on the path alone. Hosted
// serves a server-side `path_class`; switch to it once the OSS endpoint
// carries one.
const TEST_SEGMENTS = new Set([
  "test",
  "tests",
  "__tests__",
  "__test__",
  "testdata",
  "e2e",
  "spec",
  "specs",
  "fixtures",
  "__fixtures__",
  "mock",
  "mocks",
  "__mocks__",
]);
const DOCS_SEGMENTS = new Set(["docs", "doc", "documentation", "example", "examples"]);
const TEST_FILE = /(^test_|_test\.|\.test\.|\.spec\.|_spec\.)/i;
const DOCS_FILE = /\.(md|mdx|rst|adoc|txt)$/i;

/** Shipped source, tests, or docs, judged from the path. */
export function securityPathClass(path: string): SecurityPathClass {
  const parts = path.replace(/\\/g, "/").toLowerCase().split("/");
  const name = parts[parts.length - 1] ?? "";
  if (parts.some((p) => TEST_SEGMENTS.has(p)) || TEST_FILE.test(name)) return "test";
  if (parts.some((p) => DOCS_SEGMENTS.has(p)) || DOCS_FILE.test(name)) return "docs";
  return "source";
}

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
  high: number;
  med: number;
  low: number;
}

export function countSecurityFindings(findings: SecurityFinding[]): SecurityPostureCounts {
  const c: SecurityPostureCounts = {
    total: findings.length,
    source: 0,
    elsewhere: 0,
    sourceHigh: 0,
    high: 0,
    med: 0,
    low: 0,
  };
  for (const f of findings) {
    const level = securityLevel(f.severity);
    c[level] += 1;
    if (securityPathClass(f.file_path) === "source") {
      c.source += 1;
      if (level === "high") c.sourceHigh += 1;
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
  all: string;
  elsewhere: string;
  elsewhereHint: string;
  high: string;
  highSub: (sourceHigh: number) => string;
}

const n = (v: number) => v.toLocaleString();
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
  all: "All findings",
  elsewhere: "Tests and docs",
  elsewhereHint: "Matches under test, spec, fixture, mock, example or docs paths, and in Markdown or text files.",
  high: "High",
  highSub: (v) => `${n(v)} in shipped source`,
};

export interface SecurityPostureProps {
  findings: SecurityFinding[];
  /** Relative scan time ("3 days ago"), or null when the server has none. */
  scannedAgo: string | null;
  /** True when the list was cut at the request limit, so counts are a floor. */
  capped?: boolean;
  labels?: Partial<SecurityPostureLabels>;
}

/**
 * The security page's lede: the figure to act on (matches in shipped source),
 * the sentence that scopes it, a hairline ribbon, and when the scan ran.
 * Never coloured: a pattern match is a lead to confirm, not a live path to
 * harm.
 */
export function SecurityPosture({ findings, scannedAgo, capped = false, labels }: SecurityPostureProps) {
  const l = { ...DEFAULT_SECURITY_POSTURE_LABELS, ...labels };
  const c = React.useMemo(() => countSecurityFindings(findings), [findings]);

  const stats: RibbonStat[] = [
    { label: l.all, value: `${n(c.total)}${capped ? "+" : ""}` },
    { label: l.elsewhere, value: n(c.elsewhere), hint: l.elsewhereHint },
    { label: l.high, value: n(c.high), sub: l.highSub(c.sourceHigh) },
  ];

  return (
    <div className="space-y-6">
      <PageLede
        label={l.figure}
        labelHint={l.figureHint}
        value={n(c.source)}
        unit={l.unit(c.total, capped)}
        layout="beside"
      >
        <p>{l.sentence(c)}</p>
        <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
          {scannedAgo ? l.scanned(scannedAgo) : l.scanTimeUnknown}
        </p>
      </PageLede>
      <StatRibbon stats={stats} />
    </div>
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
      <Skeleton className="h-[72px] w-full rounded-none" />
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
