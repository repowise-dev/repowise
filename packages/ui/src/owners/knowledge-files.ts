/**
 * Shared readings over the knowledge matrix: who a row index is, whether a
 * file has one author, whether its health is weak, and how areas sort. The
 * off-boarding simulation, the knowledge-risk grouping and the person page all
 * read files through these, so a file means the same thing on every surface.
 *
 * Pure, no React. An "area" here is a file's own directory, the unit the
 * matrix already indexes, so grouping costs nothing.
 */

import { bandForScore, isWeakHealthBand } from "@repowise-dev/types/health";
import {
  knowledgeMatrixPath,
  type KnowledgeMatrix,
  type KnowledgeMatrixFile,
} from "@repowise-dev/types/owners";

export interface KnowledgePerson {
  key: string;
  name: string;
  email: string | null;
}

/** "A", "A and B", "A, B and C": people named in a sentence. */
export function joinNames(names: readonly string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The person at a matrix author index. */
export function personAt(matrix: KnowledgeMatrix, index: number): KnowledgePerson {
  const a = matrix.authors[index];
  return a
    ? { key: a.key, name: a.name || a.email || a.key, email: a.email }
    : { key: String(index), name: "unknown", email: null };
}

/**
 * One author holds the file: the indexer's bus factor is exactly 1. A bus
 * factor of 0 means no history was indexed, which is not a risk.
 */
export function isSingleOwner(busFactor: number): boolean {
  return busFactor === 1;
}

/** Weak health by the canonical bands; a file without a score is not weak. */
export function isLowHealth(score: number | null): boolean {
  return score != null && isWeakHealthBand(bandForScore(score));
}

/** The facts every list row shows about one matrix file. */
export interface KnowledgeFileFacts {
  path: string;
  dir: string;
  nloc: number;
  healthScore: number | null;
  isHotspot: boolean;
  lowHealth: boolean;
  /** A commit in the 90 days before HEAD. */
  changed90d: boolean;
}

export function fileFacts(matrix: KnowledgeMatrix, file: KnowledgeMatrixFile): KnowledgeFileFacts {
  return {
    path: knowledgeMatrixPath(matrix.dirs, file),
    dir: matrix.dirs[file[0]] ?? "",
    nloc: file[2],
    healthScore: file[3],
    isHotspot: file[4],
    lowHealth: isLowHealth(file[3]),
    changed90d: file[8],
  };
}

/** Hotspot and weak health together first, then either, then by size. */
export function compareFileFacts(a: KnowledgeFileFacts, b: KnowledgeFileFacts): number {
  const weight = (f: KnowledgeFileFacts) => Number(f.isHotspot) + Number(f.lowHealth);
  return weight(b) - weight(a) || b.nloc - a.nloc || a.path.localeCompare(b.path);
}

/** Areas with the most severe files first, then the most files, then the most code. */
export function compareAreas(
  a: { severe: number; fileCount: number; nloc: number; dir: string },
  b: { severe: number; fileCount: number; nloc: number; dir: string },
): number {
  return (
    b.severe - a.severe || b.fileCount - a.fileCount || b.nloc - a.nloc || a.dir.localeCompare(b.dir)
  );
}

/**
 * The person most often named across `picks`, ties broken by the code they
 * cover. Used to suggest one name per area rather than one per file.
 */
export function mostNamed(
  picks: Iterable<{ person: KnowledgePerson; nloc: number }>,
): { person: KnowledgePerson; files: number } | null {
  type Tally = { person: KnowledgePerson; files: number; nloc: number };
  const tally = new Map<string, Tally>();
  for (const { person, nloc } of picks) {
    const t = tally.get(person.key) ?? { person, files: 0, nloc: 0 };
    t.files += 1;
    t.nloc += nloc;
    tally.set(person.key, t);
  }
  const ranked = [...tally.values()].sort((a, b) => b.files - a.files || b.nloc - a.nloc);
  return ranked[0] ? { person: ranked[0].person, files: ranked[0].files } : null;
}

/** Items bucketed by their directory, buckets in first-seen order. */
export function groupByDir<T extends { dir: string }>(items: readonly T[]): Map<string, T[]> {
  const byDir = new Map<string, T[]>();
  for (const item of items) {
    const bucket = byDir.get(item.dir);
    if (bucket) bucket.push(item);
    else byDir.set(item.dir, [item]);
  }
  return byDir;
}
