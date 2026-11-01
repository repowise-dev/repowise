/**
 * The facts a change review reads from one blast-radius result. Pure: the
 * same result always reads the same way. The picture is laid out from these
 * in `blast-step-model.ts`.
 *
 * Tests are counted apart: a test that imports the changed code is a test to
 * run, never code the change reaches. Counting them as reach made a one-file
 * change read as "2,295 files reached", 2,095 of them tests.
 */

import type { AffectedSignal, BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { HEALTH_BAND_LABEL, formatScore, isWeakHealthBand, bandForScore } from "@repowise-dev/types/health";

type Result = BlastRadiusResponse & { affected_signals?: AffectedSignal[] };

/** What the index knows about a file. `null` is unknown, never "fine". */
export interface FileMarks {
  path: string;
  healthScore: number | null;
  isHotspot: boolean | null;
  /** Listed by the analysis as having no test evidence at all. */
  untested: boolean;
  /** The file is itself a test (only ever set for changed files; reach keeps tests apart). */
  isTest?: boolean;
  /** New since the last index run: no graph, health or owner yet, so none is claimed. */
  notIndexed?: boolean;
}

/** A file the change reaches: how many imports away, through which file, in which module. */
export interface ReachedFile extends FileMarks {
  depth: number;
  via: string | null;
  module: string;
}

/** One analysis read once: changed files, the source files it reaches, the tests apart. */
export interface Reach {
  changed: FileMarks[];
  /** Source files that import the change, nearest first. Tests excluded. */
  files: ReachedFile[];
  /** Test files that import the change at any depth: run them, never count them as reach. */
  tests: string[];
  direct: number;
  modules: number;
  maxHop: number;
}

/** A file's module: the index's own when health scored it, else its folder two levels down. */
export function moduleOf(path: string, signal: AffectedSignal | undefined): string {
  if (signal?.module) return signal.module;
  const parts = path.split("/");
  if (parts.length <= 1) return "(root)";
  return parts.slice(0, Math.min(2, parts.length - 1)).join("/");
}

const TEST_PATH = /(^|\/)(tests?|__tests__|specs?)\/|(^|\/)test_[^/]+\.py$|_test\.(py|go|rb)$|\.(test|spec)\.[cm]?[jt]sx?$/;

/**
 * Whether a path is a test: the index says so, or it is named the way every
 * common runner names tests (an older server sends no flag, and an index can
 * miss a suite its config does not declare).
 */
export function isTestFile(path: string, signal: AffectedSignal | undefined): boolean {
  return Boolean(signal?.is_test) || TEST_PATH.test(path);
}

export function isWeak(m: FileMarks): boolean {
  return m.healthScore !== null && isWeakHealthBand(bandForScore(m.healthScore));
}

/** How much a file asks for attention: changed often, weak health, no tests. */
export function attention(m: FileMarks): number {
  return (m.isHotspot ? 1 : 0) + (isWeak(m) ? 1 : 0) + (m.untested ? 1 : 0);
}

/** Every mark a file carries, in words, for tooltips and the review prompt. */
export function describeMarks(m: FileMarks): string[] {
  const words: string[] = [];
  if (m.healthScore !== null)
    words.push(`${HEALTH_BAND_LABEL[bandForScore(m.healthScore)]} health ${formatScore(m.healthScore)}`);
  else words.push("Health not scored");
  if (m.isHotspot) words.push("Changed often");
  if (m.untested) words.push("No test found");
  if (m.notIndexed) words.push("New since the last index: health, owners and importers not known yet");
  return words;
}

function byAttentionThenPath(a: FileMarks, b: FileMarks): number {
  return attention(b) - attention(a) || a.path.localeCompare(b.path);
}

const PACKAGE_INIT = /(^|\/)__init__\.py$/;

function marksReader(result: Result): (path: string) => FileMarks {
  const signals = new Map((result.affected_signals ?? []).map((s) => [s.path, s]));
  const gaps = new Set(result.test_gaps);
  // Without signals (an older server, a manual file pick there) the changed
  // files still carry the analysis's own hotspot verdict.
  const hotspotFallback = new Map(result.direct_risks.map((d) => [d.path, d.is_hotspot]));
  return (path) => {
    const s = signals.get(path);
    return {
      path,
      healthScore: s?.health_score ?? null,
      isHotspot: s ? s.is_hotspot : (hotspotFallback.get(path) ?? null),
      // A test is what tests the code, and a package's `__init__.py` is
      // tested through the modules it gathers: neither needs a test of its own.
      // A file the index does not have yet has no test evidence either way.
      untested: gaps.has(path) && !isTestFile(path, s) && !PACKAGE_INIT.test(path) && s?.indexed !== false,
      isTest: isTestFile(path, s),
      notIndexed: s?.indexed === false,
    };
  };
}

/** Read one result into the facts every part of the review shows. */
export function readReach(result: Result, changedFiles: string[]): Reach {
  const signals = new Map((result.affected_signals ?? []).map((s) => [s.path, s]));
  const marks = marksReader(result);
  const files: ReachedFile[] = [];
  const tests: string[] = [];
  for (const t of result.transitive_affected) {
    const signal = signals.get(t.path);
    if (isTestFile(t.path, signal)) tests.push(t.path);
    // "No test" is said of changed files only: whether a dependent is tested is
    // not about this change, and the direct-import rule behind it undercounts.
    else
      files.push({ ...marks(t.path), untested: false, depth: t.depth, via: t.via ?? null, module: moduleOf(t.path, signal) });
  }
  return {
    changed: changedFiles.map(marks).sort(byAttentionThenPath),
    files,
    tests,
    direct: files.filter((f) => f.depth === 1).length,
    modules: new Set(files.map((f) => f.module)).size,
    maxHop: files.reduce((m, f) => Math.max(m, f.depth), 0),
  };
}

/**
 * Files to look at first: changed or one import away, and marked as changed
 * often, weak health or untested. Changed files lead; then the most marks.
 */
export function riskySpots(reach: Reach, limit = 5): (FileMarks & { changed: boolean })[] {
  return [
    ...reach.changed.map((m) => ({ ...m, changed: true })),
    ...reach.files.filter((f) => f.depth === 1).map((f) => ({ ...f, changed: false })),
  ]
    .filter((n) => attention(n) > 0)
    .sort((a, b) => Number(b.changed) - Number(a.changed) || attention(b) - attention(a) || a.path.localeCompare(b.path))
    .slice(0, limit);
}
