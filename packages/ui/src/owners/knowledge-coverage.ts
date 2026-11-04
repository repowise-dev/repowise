/**
 * Knowledge coverage: of the code changed in the last 90 days, how much has
 * two or more people who know it (bus factor 2 or more). Files with no
 * indexed history (bus factor 0) are left out of both sides. Weighted by
 * lines of code; when no changing file has a measured size, by file count.
 *
 * Pure and O(files). The single-holder changing files come back grouped by
 * directory for the "spread the knowledge" prompt.
 */

import type { KnowledgeMatrix } from "@repowise-dev/types/owners";
import { isSingleOwner } from "./knowledge-files";
import { singleHolderFiles, type KnowledgeRiskArea } from "./knowledge-risks";

/**
 * At or above this share the lede reads as good news (calm green); below it,
 * plain. A product judgement, not a health band: half the moving code with a
 * second reader is where a single absence stops stalling most work.
 */
export const WELL_SHARED_MIN = 0.5;

export interface KnowledgeCoverage {
  /** Changing files with indexed history. */
  files: number;
  /** Of those, files two or more people hold. */
  sharedFiles: number;
  nloc: number;
  sharedNloc: number;
  /** Shared share (0-1) by `basis`; null when no file with history changed. */
  share: number | null;
  basis: "nloc" | "files";
  /** Changing files one person holds, by directory, most severe first. */
  singleHolderAreas: KnowledgeRiskArea[];
  singleHolderFiles: number;
}

/** Two or more people hold it; a file with no history is neither shared nor single. */
export function isSharedKnowledge(busFactor: number): boolean {
  return busFactor > 0 && !isSingleOwner(busFactor);
}

export function knowledgeCoverage(matrix: KnowledgeMatrix): KnowledgeCoverage {
  let files = 0;
  let sharedFiles = 0;
  let nloc = 0;
  let sharedNloc = 0;
  for (const row of matrix.files) {
    if (!row[8] || row[5] <= 0) continue;
    files += 1;
    nloc += row[2];
    if (isSharedKnowledge(row[5])) {
      sharedFiles += 1;
      sharedNloc += row[2];
    }
  }
  const basis = nloc > 0 ? "nloc" : "files";
  const share = files === 0 ? null : basis === "nloc" ? sharedNloc / nloc : sharedFiles / files;
  const single = singleHolderFiles(matrix, (f) => f.changed90d);
  return {
    files,
    sharedFiles,
    nloc,
    sharedNloc,
    share,
    basis,
    singleHolderAreas: single.areas,
    singleHolderFiles: single.files.length,
  };
}
