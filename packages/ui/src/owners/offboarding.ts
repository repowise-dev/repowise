/**
 * Off-boarding what-if: if these people left, which files would lose most of
 * the knowledge behind them, and who is left to take each area over.
 *
 * Rule (D4): a file is at risk when the authors who stay hold less than
 * `threshold` of its commits. The matrix lists each file's top five people;
 * anyone outside them holds less than the fifth share, so a leaver outside the
 * top five is read as holding none, and everything not lost counts as staying.
 * A file is severe when it is also a hotspot or in a weak health band.
 *
 * Pure and O(files) per call; the result type is the contract the Ownership
 * page, the person page and the area map's off-boarding lens all render.
 */

import type {
  KnowledgeMatrix,
  KnowledgeMatrixFile,
  KnowledgeMatrixShare,
} from "@repowise-dev/types/owners";
import {
  compareAreas,
  compareFileFacts,
  fileFacts,
  groupByDir,
  mostNamed,
  personAt,
  type KnowledgeFileFacts,
  type KnowledgePerson,
} from "./knowledge-files";

/** D4's default: at risk when the people who stay hold under half the commits. */
export const DEFAULT_OFFBOARDING_THRESHOLD = 0.5;

/** `at_risk`: the rule above. `severe`: at risk and a hotspot or weak health. */
export type OffboardingSeverity = "at_risk" | "severe";

export interface OffboardingFile extends KnowledgeFileFacts {
  /** Share of the file's commits the leavers made (0-1). */
  lostShare: number;
  /** 1 - lostShare. */
  remainingShare: number;
  severity: OffboardingSeverity;
  /** The strongest author who stays, with their share; null when nobody listed stays. */
  inheritor: (KnowledgePerson & { share: number }) | null;
}

export interface OffboardingArea {
  /** Directory; "" is the repository root. */
  dir: string;
  /** At-risk files, severe first, then by size. */
  files: OffboardingFile[];
  fileCount: number;
  severe: number;
  hotspots: number;
  nloc: number;
  /** Who most often stays as the strongest author here, and on how many of these files. */
  inheritor: { person: KnowledgePerson; files: number } | null;
}

export interface OffboardingTotals {
  /** Files with a share held by a leaver. */
  touchedFiles: number;
  atRiskFiles: number;
  severeFiles: number;
  hotspots: number;
  atRiskNloc: number;
  /** NLOC across the files in scope (every file, or only changing ones). */
  totalNloc: number;
  /** atRiskNloc / totalNloc, 0 when the matrix has no code. */
  nlocShare: number;
  /** Files in scope. */
  trackedFiles: number;
}

export interface OffboardingResult {
  /** The leavers found in the matrix, in the order asked. */
  leaving: KnowledgePerson[];
  threshold: number;
  totals: OffboardingTotals;
  /** Directories with at-risk files; together they hold every at-risk file once. */
  areas: OffboardingArea[];
}

/** Matrix indices of the leavers it knows, in the order asked. */
function leaverIndices(matrix: KnowledgeMatrix, keys: readonly string[]): Set<number> {
  const keyIndex = new Map(matrix.authors.map((a, i) => [a.key, i]));
  return new Set(keys.flatMap((key) => (keyIndex.has(key) ? [keyIndex.get(key)!] : [])));
}

/** What the leavers hold of one file, and the strongest author who stays. */
function fileExposure(
  shares: KnowledgeMatrixShare[],
  leaving: ReadonlySet<number>,
): { lost: number; heir: KnowledgeMatrixShare | null } {
  let lost = 0;
  let heir: KnowledgeMatrixShare | null = null;
  for (const entry of shares) {
    if (leaving.has(entry[0])) lost += entry[1];
    else if (!heir || entry[1] > heir[1]) heir = entry;
  }
  return { lost, heir };
}

function atRiskFile(
  matrix: KnowledgeMatrix,
  row: KnowledgeMatrixFile,
  lost: number,
  heir: KnowledgeMatrixShare | null,
): OffboardingFile {
  const facts = fileFacts(matrix, row);
  return {
    ...facts,
    lostShare: Math.min(1, lost),
    // Rounded shares can sum a hair past 1; clamp so "remaining" never reads negative.
    remainingShare: Math.max(0, 1 - lost),
    severity: facts.isHotspot || facts.lowHealth ? "severe" : "at_risk",
    inheritor: heir ? { ...personAt(matrix, heir[0]), share: heir[1] } : null,
  };
}

const isSevere = (f: OffboardingFile) => f.severity === "severe";
const isHotspot = (f: OffboardingFile) => f.isHotspot;
const nlocOf = (files: readonly OffboardingFile[]) => files.reduce((s, f) => s + f.nloc, 0);

function rollUpAreas(files: OffboardingFile[]): OffboardingArea[] {
  return Array.from(groupByDir(files), ([dir, group]) => ({
    dir,
    files: group,
    fileCount: group.length,
    severe: group.filter(isSevere).length,
    hotspots: group.filter(isHotspot).length,
    nloc: nlocOf(group),
    inheritor: mostNamed(
      group.flatMap((f) => (f.inheritor ? [{ person: f.inheritor, nloc: f.nloc }] : [])),
    ),
  })).sort(compareAreas);
}

export interface OffboardingOptions {
  /**
   * Only files changed in the last 90 days count: code still moving, as
   * knowledge loss reads it. Every total, denominators included, is then
   * over those files.
   */
  changedOnly?: boolean;
}

export function simulateOffboarding(
  matrix: KnowledgeMatrix,
  leavingKeys: readonly string[],
  threshold: number = DEFAULT_OFFBOARDING_THRESHOLD,
  { changedOnly = false }: OffboardingOptions = {},
): OffboardingResult {
  const leaving = leaverIndices(matrix, leavingKeys);
  const files: OffboardingFile[] = [];
  let touchedFiles = 0;
  let trackedFiles = 0;
  let totalNloc = 0;
  for (const row of matrix.files) {
    if (changedOnly && !row[8]) continue;
    trackedFiles += 1;
    totalNloc += row[2];
    const { lost, heir } = fileExposure(row[7], leaving);
    if (lost <= 0) continue;
    touchedFiles += 1;
    if (1 - lost < threshold) files.push(atRiskFile(matrix, row, lost, heir));
  }
  files.sort(compareFileFacts);

  const atRiskNloc = nlocOf(files);
  return {
    leaving: Array.from(leaving, (i) => personAt(matrix, i)),
    threshold,
    totals: {
      touchedFiles,
      atRiskFiles: files.length,
      severeFiles: files.filter(isSevere).length,
      hotspots: files.filter(isHotspot).length,
      atRiskNloc,
      totalNloc,
      nlocShare: totalNloc > 0 ? atRiskNloc / totalNloc : 0,
      trackedFiles,
    },
    areas: rollUpAreas(files),
  };
}

// ---------------------------------------------------------------------------
// URL state: `?leave=a,b&threshold=50`
// ---------------------------------------------------------------------------

/** Owner keys can hold a comma (`name:Doe, Jane`), so each one escapes it. */
export function formatLeaveParam(keys: readonly string[]): string {
  return keys.map((k) => k.replaceAll("%", "%25").replaceAll(",", "%2C")).join(",");
}

export function parseLeaveParam(raw: string | null | undefined): string[] {
  if (!raw) return [];
  const keys = raw
    .split(",")
    .map((k) => k.replaceAll("%2C", ",").replaceAll("%25", "%"))
    .filter(Boolean);
  return Array.from(new Set(keys));
}

/** The thresholds the page offers, in whole percent. */
export const OFFBOARDING_THRESHOLD_RANGE = { min: 10, max: 90, step: 5 } as const;

/**
 * Whole percent in the URL, clamped to the offered range so a hand-edited
 * link never shows a figure the slider cannot; anything unparsable falls
 * back to the default.
 */
export function parseThresholdParam(raw: string | null | undefined): number {
  const n = raw == null || raw === "" ? NaN : Number(raw);
  if (!Number.isInteger(n)) return DEFAULT_OFFBOARDING_THRESHOLD;
  const { min, max } = OFFBOARDING_THRESHOLD_RANGE;
  return Math.min(max, Math.max(min, n)) / 100;
}

export function formatThresholdParam(threshold: number): string {
  return String(Math.round(threshold * 100));
}
