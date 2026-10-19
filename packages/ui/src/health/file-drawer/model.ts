/**
 * Everything the file drawer derives from its props, in one hook, so the
 * drawer itself is a composition of sections that only render.
 *
 * Pure functions do the work (which findings lead, how the rest group, what
 * the verdict may honestly say, what fixing the top issue gives back); the
 * hook memoizes them against the triage state the drawer keeps for the file.
 */

import { useCallback, useMemo, useState } from "react";
import {
  asBiomarkerDimension,
  biomarkerInfo,
  CATEGORY_CAP,
  isHistoryBiomarker,
} from "../biomarker-glossary";
import type { ScoreBreakdownCategory } from "../score-breakdown";
import { fileVerdict, type Verdict } from "../plain-language";
import { relatedWorkCount, type RelatedLensName } from "../related-work";
import type {
  HealthDrawerFinding,
  HealthDrawerMetric,
  HealthFileDrawerProps,
} from "../health-file-drawer";
import { CLOSED_STATUSES, POOLED_FILE_KEY, TOP_FINDINGS } from "./shared";

/** Stable empty list, so a host passing no findings does not churn the memos. */
const NO_FINDINGS: HealthDrawerFinding[] = [];

export type StatusOverrides = Readonly<Record<string, string>>;

export function statusOf(f: HealthDrawerFinding, overrides: StatusOverrides): string {
  return overrides[f.id] ?? f.status ?? "open";
}

function isOpen(f: HealthDrawerFinding, overrides: StatusOverrides): boolean {
  return !CLOSED_STATUSES.has(statusOf(f, overrides));
}

/** One "What's wrong" row: the worst instance of a kind and every place it fires. */
export interface TopIssue {
  lead: HealthDrawerFinding;
  /** Other places the same kind fires. */
  more: number;
  members: HealthDrawerFinding[];
}

/**
 * Whether a finding may lead "What's wrong". History markers are scored but no
 * edit here clears them; one that takes nothing off is not a reason the file
 * scores low; a closed one is already decided; and under the performance lens
 * its findings are the causes block above, so they do not lead a second time.
 */
function canLead(f: HealthDrawerFinding, overrides: StatusOverrides, lens: string | undefined) {
  if (isHistoryBiomarker(f.biomarker_type)) return false;
  if (!(f.health_impact > 0)) return false;
  if (!isOpen(f, overrides)) return false;
  const performanceHere =
    lens === "performance" && asBiomarkerDimension(f.dimension, f.biomarker_type) === "performance";
  return !performanceHere;
}

/**
 * One row per kind of problem: three "Errors caught too broadly" rows read as
 * three problems when they are one, so a kind shows its worst instance and
 * says how many more places it fires.
 */
export function pickTopIssues(
  findings: readonly HealthDrawerFinding[],
  overrides: StatusOverrides,
  lens: string | undefined,
): TopIssue[] {
  const byKind = new Map<string, HealthDrawerFinding[]>();
  for (const f of findings) {
    if (!canLead(f, overrides, lens)) continue;
    const bucket = byKind.get(f.biomarker_type);
    if (bucket) bucket.push(f);
    else byKind.set(f.biomarker_type, [f]);
  }
  return [...byKind.values()]
    .map((members) => ({ lead: worstOf(members), more: members.length - 1, members }))
    .sort((a, b) => b.lead.health_impact - a.lead.health_impact)
    .slice(0, TOP_FINDINGS);
}

function worstOf(group: readonly HealthDrawerFinding[]): HealthDrawerFinding {
  return group.reduce((a, b) => (b.health_impact > a.health_impact ? b : a));
}

/** What the open findings below the lead still take off, and how much is history. */
function tallyRest(rest: readonly HealthDrawerFinding[], overrides: StatusOverrides) {
  let count = 0;
  let impact = 0;
  let historyImpact = 0;
  for (const f of rest) {
    if (!isOpen(f, overrides)) continue;
    const off = Math.max(0, f.health_impact);
    count += 1;
    impact += off;
    if (isHistoryBiomarker(f.biomarker_type)) historyImpact += off;
  }
  return { count, impact, historyImpact };
}

/**
 * What fixing the top issue would give back, only when the numbers support
 * saying so: its category must not be held at a cap (removing one of many
 * capped issues changes nothing), and the score must not be clamped at the
 * floor (then the deduction overshoots what the score shows).
 */
export function topIssueRecovery(
  metric: HealthDrawerMetric,
  breakdown: HealthFileDrawerProps["breakdown"],
  members: readonly HealthDrawerFinding[] | undefined,
): { points: number; places: number } | null {
  if (!members?.length || !breakdown || metric.score == null) return null;
  const cat = biomarkerInfo(members[0]!.biomarker_type).category;
  const row = breakdown.categories.find((c) => String(c.category) === cat);
  if (!row || row.capped) return null;
  if (Math.abs(10 - breakdown.total_deduction - metric.score) > 0.05) return null;
  // Every place the row covers, never more than the category actually took off
  // or than the score has room to rise.
  const sum = members.reduce((n, f) => n + Math.max(0, f.health_impact), 0);
  const gain = Math.min(sum, row.applied_deduction, 10 - metric.score);
  return gain >= 0.1 ? { points: Math.round(gain * 10) / 10, places: members.length } : null;
}

export interface FindingSection {
  key: string;
  group: HealthDrawerFinding[];
  isFile: boolean;
  total: number;
  worst: HealthDrawerFinding;
  /** The enforced cap when this group's category is holding at it, else null. */
  atCap: number | null;
}

/** Categories the scorer held at their ceiling, per the server breakdown.
 *  `capped` is the server's verdict and is never recomputed here; the cap
 *  VALUE is display only, so it falls back to the glossary mirror. */
function cappedCategories(categories: readonly ScoreBreakdownCategory[]) {
  const capped = new Map<string, number | null>();
  for (const c of categories) {
    if (c.capped) capped.set(String(c.category), c.cap ?? null);
  }
  return capped;
}

/** By function, and file-level ones by biomarker. Two maps, never one keyed by
 *  a sentinel string: a C++ or Rust function can legitimately be `file::read`,
 *  so any "file" prefix is collidable. */
function bucketFindings(findings: readonly HealthDrawerFinding[]) {
  const byFunction = new Map<string, HealthDrawerFinding[]>();
  const byBiomarker = new Map<string, HealthDrawerFinding[]>();
  for (const f of findings) {
    const map = f.function_name ? byFunction : byBiomarker;
    const key = f.function_name ?? f.biomarker_type;
    const bucket = map.get(key);
    if (bucket) bucket.push(f);
    else map.set(key, [f]);
  }
  return { byFunction, byBiomarker };
}

/** Repeating file-level biomarkers get a group each; one-offs pool into one. */
function fileLevelGroups(byBiomarker: Map<string, HealthDrawerFinding[]>) {
  const pooled: HealthDrawerFinding[] = [];
  const groups: { key: string; group: HealthDrawerFinding[] }[] = [];
  for (const [key, group] of byBiomarker) {
    if (group.length > 1) groups.push({ key, group });
    else pooled.push(...group);
  }
  if (pooled.length > 0) groups.push({ key: POOLED_FILE_KEY, group: pooled });
  return groups;
}

/**
 * The cap to name on a file-level group, or null. Claimed only when this group
 * IS the category, decided from the findings on screen: a host may scope them
 * differently from the server's subtotal (hosted excludes triaged findings
 * from one).
 */
function groupCap(
  key: string,
  worst: HealthDrawerFinding,
  allFindings: readonly HealthDrawerFinding[],
  capped: Map<string, number | null>,
): number | null {
  if (key === POOLED_FILE_KEY) return null;
  const cat = biomarkerInfo(worst.biomarker_type).category;
  if (!capped.has(cat)) return null;
  const owns = allFindings.every(
    (f) =>
      biomarkerInfo(f.biomarker_type).category !== cat ||
      f.biomarker_type === worst.biomarker_type,
  );
  return owns ? (capped.get(cat) ?? CATEGORY_CAP[cat] ?? null) : null;
}

function describeSection(
  key: string,
  group: HealthDrawerFinding[],
  isFile: boolean,
  allFindings: readonly HealthDrawerFinding[],
  capped: Map<string, number | null>,
): FindingSection {
  // Inside a single-biomarker group every marker carries the same label and
  // impact, so the line is the only axis a reader can follow.
  const ordered =
    isFile && key !== POOLED_FILE_KEY
      ? [...group].sort((a, b) => (a.line_start ?? Infinity) - (b.line_start ?? Infinity))
      : group;
  const total = ordered.reduce((s, f) => s + f.health_impact, 0);
  const worst = worstOf(ordered);
  const atCap = isFile ? groupCap(key, worst, allFindings, capped) : null;
  return { key, group: ordered, isFile, total, worst, atCap };
}

/**
 * Group findings by the function they fire on, so one oversized function reads
 * as a single collapsible group instead of N sibling rows.
 *
 * A file-level biomarker gets its own group only when it actually repeats:
 * `error_handling` sets no function_name and fires once per occurrence, so one
 * file in this repo reaches 34 sibling rows worth 0.51 points. Splitting every
 * file-level biomarker out was measurably worse (53% of files carry 2+
 * distinct one-off file-level markers), so the one-offs stay pooled.
 */
export function groupFindings(
  findings: readonly HealthDrawerFinding[],
  categories: readonly ScoreBreakdownCategory[],
  /** Every finding on the file, for the cap's ownership test. */
  allFindings: readonly HealthDrawerFinding[] = findings,
): FindingSection[] {
  const capped = cappedCategories(categories);
  const { byFunction, byBiomarker } = bucketFindings(findings);
  return [
    ...[...byFunction.entries()].map(([key, group]) =>
      describeSection(key, group, false, allFindings, capped),
    ),
    ...fileLevelGroups(byBiomarker).map(({ key, group }) =>
      describeSection(key, group, true, allFindings, capped),
    ),
  ].sort((a, b) => b.total - a.total);
}

/** Where a finding fires, and the link to it when the host has one. */
export function findingLocation(
  f: HealthDrawerFinding,
  fileViewHrefFor: ((lineStart: number) => string) | undefined,
): { label: string; href: string | undefined } | null {
  // Anchored on line_start, not function_name: `error_handling` fires per
  // occurrence with a precise line and no function, and withholding the line
  // made its rows read as duplicates.
  if (!f.function_name && f.line_start == null) return null;
  const label = f.function_name
    ? `${f.function_name}${f.line_start != null ? `:${f.line_start}` : ""}`
    : `line ${f.line_start}`;
  // A link only when it goes to the line. Falling back to the file page made
  // every row link to one page, which "Open full page" already is.
  const href =
    f.line_start != null && fileViewHrefFor ? fileViewHrefFor(f.line_start) : undefined;
  return { label, href };
}

/** The drawer's per-file triage state: in-session status changes and the write in flight. */
export function useTriage(
  onFindingStatusChange: HealthFileDrawerProps["onFindingStatusChange"],
) {
  const [overrides, setOverrides] = useState<StatusOverrides>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const setStatus = useCallback(
    async (id: string, status: string) => {
      if (!onFindingStatusChange) return;
      setSavingId(id);
      try {
        await onFindingStatusChange(id, status);
        setOverrides((m) => ({ ...m, [id]: status }));
      } finally {
        setSavingId(null);
      }
    },
    [onFindingStatusChange],
  );
  return {
    overrides,
    savingId,
    setStatus: onFindingStatusChange ? setStatus : undefined,
  };
}

export interface DrawerModel {
  findings: HealthDrawerFinding[];
  topIssues: TopIssue[];
  /** Everything not leading, for "Other issues". */
  rest: HealthDrawerFinding[];
  sections: FindingSection[];
  verdict: Verdict;
  recovers: { points: number; places: number } | null;
  heldBackTotal: number;
  /** The one primary action is offered unless the verdict is the all-clear. */
  showPrompt: boolean;
  relatedExclude: readonly RelatedLensName[];
  relatedTally: { items: number; lenses: number };
}

export function useDrawerModel(
  props: HealthFileDrawerProps & { metric: HealthDrawerMetric },
  overrides: StatusOverrides,
): DrawerModel {
  const { metric, breakdown, loading, error, heldBack, lens, performance, related } = props;
  const findings = props.findings ?? NO_FINDINGS;
  // A host that has not handed findings over, or is still fetching or failed,
  // has not shown the file is clean, so the verdict claims nothing.
  const findingsKnown = props.findings !== undefined && !loading && error == null;

  const topIssues = useMemo(
    () => pickTopIssues(findings, overrides, lens),
    [findings, overrides, lens],
  );
  // A row already in What's wrong is not drawn twice. The cap check still
  // reads every finding, because a category is owned by the findings on the
  // file, not by the ones left below.
  const rest = useMemo(() => {
    const shown = new Set(topIssues.map((i) => i.lead.id));
    return findings.filter((f) => !shown.has(f.id));
  }, [findings, topIssues]);
  const sections = useMemo(
    () => groupFindings(rest, breakdown?.categories ?? [], findings),
    [rest, findings, breakdown],
  );

  const verdict = useMemo(() => {
    const open = findings.filter((f) => isOpen(f, overrides));
    return fileVerdict({
      score: metric.score,
      // Every place each leading kind fires, so "two functions" counts them all.
      top: topIssues.flatMap((i) => i.members),
      hasTestFile: metric.has_test_file,
      historyOnly: open.length > 0 && open.every((f) => isHistoryBiomarker(f.biomarker_type)),
      findingsKnown,
      rest: tallyRest(rest, overrides),
    });
  }, [metric.score, metric.has_test_file, findings, topIssues, rest, overrides, findingsKnown]);

  const heldBackTotal = useMemo(
    () => Object.values(heldBack ?? {}).reduce((n, g) => n + g.count, 0),
    [heldBack],
  );

  const relatedExclude: readonly RelatedLensName[] =
    lens === "performance" ? ["findings", "performance"] : ["findings"];
  const performanceCauses = lens === "performance" && (performance?.items.length ?? 0) > 0;

  return {
    findings,
    topIssues,
    rest,
    sections,
    verdict,
    recovers: topIssueRecovery(metric, breakdown, topIssues[0]?.members),
    heldBackTotal,
    showPrompt: !verdict.healthy || topIssues.length > 0 || performanceCauses,
    relatedExclude,
    relatedTally: relatedWorkCount(related, relatedExclude),
  };
}
